"""Bus de eventos: persiste los eventos no efímeros (seq creciente) y los reparte a los suscriptores en vivo.

Solo se usa desde el hilo del bucle asyncio; los hilos de audio y del worker llegan aquí con call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from voice_core.almacen import Almacen
from voice_core.contratos import EVENTO, EVENTOS_EFIMEROS, Evento

log = logging.getLogger(__name__)

MAX_COLA = 2000


def ahora() -> datetime:
    return datetime.now(UTC)


class Bus:
    def __init__(self, almacen: Almacen) -> None:
        self._almacen = almacen
        self._colas: set[asyncio.Queue[Evento | None]] = set()
        self.cerrando = False

    def cerrar(self) -> None:
        """Cierre del servicio: despierta a todos los suscriptores con el centinela para que suelten su conexión.
        Sin esto, un WebSocket abierto deja a uvicorn esperando para siempre al apagarse."""
        self.cerrando = True
        for cola in list(self._colas):
            while not cola.empty():
                cola.get_nowait()
            cola.put_nowait(None)
        self._colas.clear()

    def publicar(self, evento: Evento) -> Evento:
        if evento.type not in EVENTOS_EFIMEROS:
            payload = evento.model_dump(mode="json", exclude={"seq"})
            seq = self._almacen.guardar_evento(evento.type, payload["ts"], payload.get("session_id"),
                                               json.dumps(payload, ensure_ascii=False))
            evento = evento.model_copy(update={"seq": seq})
        for cola in list(self._colas):
            try:
                cola.put_nowait(evento)
            except asyncio.QueueFull:
                log.warning("Suscriptor lento: se desconecta")
                self._colas.discard(cola)
                while not cola.empty():
                    cola.get_nowait()
                cola.put_nowait(None)  # centinela: el consumidor debe cerrar
        return evento

    def suscribir(self) -> asyncio.Queue[Evento | None]:
        cola: asyncio.Queue[Evento | None] = asyncio.Queue(maxsize=MAX_COLA)
        self._colas.add(cola)
        return cola

    def desuscribir(self, cola: asyncio.Queue[Evento | None]) -> None:
        self._colas.discard(cola)

    def desde(self, despues: int, limite: int) -> list[Evento]:
        return [EVENTO.validate_python({**json.loads(f["payload"]), "seq": f["seq"]})
                for f in self._almacen.eventos_desde(despues, limite)]
