"""Gestor de sesiones: une micrófono, motor, almacén y bus. Una sola sesión activa a la vez."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from voice_core.ajustes import Ajustes
from voice_core.almacen import Almacen, Fila
from voice_core.audio import SR, Entrega, Fuente
from voice_core.bus import Bus, ahora
from voice_core.contratos import (EngineError, ModoSesion, SegmentFinal, SegmentPartial, SessionFinalized,
                                  SessionStarted)
from voice_core.motor import Motor

log = logging.getLogger(__name__)

FabricaFuente = Callable[[Entrega], Fuente]
Sonido = Callable[[str], None]


class SesionEnCurso(Exception):
    pass


class SesionNoActiva(Exception):
    pass


class SesionNoEncontrada(Exception):
    pass


@dataclass
class _Activa:
    id: str
    modo: ModoSesion
    inicio: datetime
    t_inicio: float
    fuente: Fuente | None = None
    audio: list[np.ndarray] = field(default_factory=list)
    segmentos: list[str] = field(default_factory=list)
    deteniendo: bool = False
    terminada: asyncio.Event = field(default_factory=asyncio.Event)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class GestorSesiones:
    def __init__(self, aj: Ajustes, almacen: Almacen, bus: Bus, motor: Motor, fabrica_fuente: FabricaFuente,
                 sonido: Sonido = lambda nombre: None) -> None:
        self._aj = aj
        self._almacen = almacen
        self._bus = bus
        self._motor = motor
        self._fabrica_fuente = fabrica_fuente
        self._sonido = sonido
        self._activa: _Activa | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cierres: set[asyncio.Task[None]] = set()
        motor.al_evento = self._al_evento_motor

    def vincular(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def activa(self) -> str | None:
        return self._activa.id if self._activa else None

    # ------------------------------------------------------------ inicio y fin

    async def iniciar(self, modo: ModoSesion) -> Fila:
        if self._activa is not None:
            raise SesionEnCurso(f"Ya hay una sesión activa: {self._activa.id}")
        dispositivo = self._motor.encender()  # puede lanzar MotorPausado o SinEspacioGpu
        inicio = ahora()
        a = _Activa(id=uuid.uuid4().hex[:12], modo=modo, inicio=inicio, t_inicio=time.monotonic())
        self._almacen.crear_sesion(a.id, modo, _iso(inicio))
        self._almacen.fijar_motor(a.id, dispositivo, self._aj.motor.modelo)
        self._activa = a
        self._motor.iniciar_sesion()
        try:
            a.fuente = self._fabrica_fuente(self._desde_audio)
            a.fuente.iniciar()
        except Exception as e:
            log.exception("No se pudo abrir el micrófono")
            self._activa = None
            self._motor.sesion_cerrada()
            self._almacen.finalizar_sesion(a.id, _iso(ahora()), "", None)
            self._bus.publicar(EngineError(seq=None, ts=ahora(), message=f"Micrófono: {e}", session_id=a.id))
            raise
        self._bus.publicar(SessionStarted(seq=None, ts=inicio, session_id=a.id, mode=modo))
        self._sonido("inicio")
        return self._almacen.sesion(a.id)  # type: ignore[return-value]

    async def detener(self, id_: str | None = None) -> Fila:
        a = self._activa
        if a is None or (id_ is not None and id_ != a.id):
            raise SesionNoActiva("Esa sesión no está grabando")
        if a.deteniendo:
            return self._almacen.sesion(a.id)  # type: ignore[return-value]
        if a.fuente is not None:
            a.fuente.detener()
        await asyncio.sleep(0)  # deja pasar el audio que el hilo del micrófono ya encoló
        a.deteniendo = True
        self._motor.terminar_sesion()
        self._almacen.marcar_estado(a.id, "procesando")
        self._sonido("fin")
        tarea = asyncio.create_task(self._esperar_cierre(a))
        self._cierres.add(tarea)
        tarea.add_done_callback(self._cierres.discard)
        return self._almacen.sesion(a.id)  # type: ignore[return-value]

    async def alternar(self, modo: ModoSesion) -> Fila:
        if self._activa is not None and not self._activa.deteniendo:
            return await self.detener()
        return await self.iniciar(modo)

    async def liberar(self, motivo: str) -> None:
        """Cierra el dictado en curso (conservando su texto final) y apaga el motor para liberar la GPU."""
        if self._activa is not None and not self._activa.deteniendo:
            await self.detener()
        await self.esperar_cierres()
        await self._motor.apagar(motivo)
        self._sonido("liberado")

    async def esperar_cierres(self) -> None:
        if self._cierres:
            await asyncio.gather(*self._cierres, return_exceptions=True)

    async def cerrar(self) -> None:
        if self._activa is not None and not self._activa.deteniendo:
            await self.detener()
        await self.esperar_cierres()

    async def _esperar_cierre(self, a: _Activa) -> None:
        espera = self._aj.motor.arranque_max_s + 60
        try:
            await asyncio.wait_for(a.terminada.wait(), timeout=espera)
        except TimeoutError:
            self._bus.publicar(EngineError(seq=None, ts=ahora(), session_id=a.id,
                                           message=f"No llegó el texto final en {espera:.0f} s"))
        self._finalizar(a)

    def _finalizar(self, a: _Activa) -> None:
        texto = " ".join(a.segmentos)
        ruta_audio = self._guardar_audio(a)
        duracion = time.monotonic() - a.t_inicio
        self._almacen.finalizar_sesion(a.id, _iso(ahora()), texto, str(ruta_audio) if ruta_audio else None)
        self._bus.publicar(SessionFinalized(seq=None, ts=ahora(), session_id=a.id, text=texto,
                                            duration_s=round(duracion, 2), segments=len(a.segmentos)))
        if self._activa is a:
            self._activa = None
        self._motor.sesion_cerrada()

    def _guardar_audio(self, a: _Activa) -> Path | None:
        if not (self._aj.datos.guardar_audio and a.audio):
            return None
        import soundfile as sf

        ruta = self._aj.carpeta_datos / "sesiones" / a.id / "audio.wav"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(ruta), np.concatenate(a.audio), SR, subtype="PCM_16")
        return ruta

    # ------------------------------------------------------------ audio y eventos del motor

    def _desde_audio(self, trozo: np.ndarray) -> None:
        """Llamado desde el hilo del micrófono."""
        if self._loop is None:
            self._al_audio(trozo)
        else:
            self._loop.call_soon_threadsafe(self._al_audio, trozo)

    def _al_audio(self, trozo: np.ndarray) -> None:
        a = self._activa
        if a is None or a.deteniendo:
            return
        a.audio.append(trozo)
        self._motor.enviar("audio", trozo)

    def _al_evento_motor(self, tipo: str, datos: dict) -> None:
        a = self._activa
        if a is None:
            return
        if tipo == "parcial" and not a.deteniendo:
            self._bus.publicar(SegmentPartial(seq=None, ts=ahora(), session_id=a.id, text=datos["texto"]))
        elif tipo == "final":
            indice = len(a.segmentos)
            a.segmentos.append(datos["texto"])
            self._almacen.agregar_segmento(a.id, indice, datos["t0"], datos["t1"], datos["texto"])
            self._bus.publicar(SegmentFinal(seq=None, ts=ahora(), session_id=a.id, index=indice, t0=datos["t0"],
                                            t1=datos["t1"], text=datos["texto"]))
        elif tipo == "vaciado":
            a.terminada.set()
        elif tipo == "perdido":
            if not a.deteniendo:
                if a.fuente is not None:
                    a.fuente.detener()
                a.deteniendo = True
                tarea = asyncio.ensure_future(self._esperar_cierre(a))
                self._cierres.add(tarea)
                tarea.add_done_callback(self._cierres.discard)
            a.terminada.set()

    # ------------------------------------------------------------ exportar

    def exportar(self, id_: str, formato: str) -> str:
        s = self._almacen.sesion(id_)
        if s is None:
            raise SesionNoEncontrada(id_)
        if formato == "txt":
            return s["text"] + "\n"
        inicio = datetime.fromisoformat(s["started_at"]).astimezone()
        titulo = "Tarea" if s["mode"] == "tarea" else "Dictado"
        motor = f"{s['model']} ({s['device']})" if s["model"] else "—"
        return f"# {titulo} · {inicio:%Y-%m-%d %H:%M}\n\n- Motor: {motor}\n- Sesión: `{s['id']}`\n\n{s['text']}\n"
