"""El organizador: escucha a voice-core, encola extracciones, llama al modelo y guarda las propuestas.

Todo corre en el bucle asyncio del servicio; lo que bloquea (LM Studio, REST de voice-core, NVML) va a hilos con
`asyncio.to_thread`. Una extracción a la vez: el modelo es uno solo y las llamadas en paralelo solo competirían.
Salvaguarda de la GPU: con la GPU de LM Studio ocupada por otro programa no se pide la carga del modelo, y el modelo
que carga el organizador se descarga en cuanto se vacía la cola.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from voice_core.ajustes import AjustesTareas
from voice_core.bus import Bus, ahora

from tasks_service.almacen import AlmacenTareas, ahora_iso
from tasks_service.contratos import (CategoryOut, ExtractionOut, ExtractionUpdated, TaskCreated, TaskIn, TaskOut,
                                     TaskPatch, TaskUpdated)
from tasks_service.llm import ErrorLlm
from tasks_service.parser import RespuestaInvalida, categorias_efectivas, esquema, interpretar, mensajes
from tasks_service.voz import SesionDesconocida, VoiceCoreNoResponde

log = logging.getLogger(__name__)

REINTENTO_WS_S = 2.0


class TareaNoEncontrada(LookupError):
    pass


class CategoriaDesconocida(ValueError):
    pass


class SesionNoTerminada(RuntimeError):
    pass


class Organizador:
    def __init__(self, aj: AjustesTareas, almacen: AlmacenTareas, bus: Bus, llm: Any, voz: Any,
                 gpu: Any = None) -> None:
        self._aj = aj
        self._almacen = almacen
        self._bus = bus
        self._llm = llm
        self._voz = voz
        self._gpu = gpu  # VigiaGpu o None (sin comprobar)
        self._modelo_nuestro = False  # lo cargó una extracción: se descarga al vaciarse la cola
        self.categorias = categorias_efectivas(aj.categorias)
        self._cola: asyncio.Queue[int] = asyncio.Queue()
        self._modos: dict[str, str] = {}  # sesión → modo, visto en session.started
        self._tareas_async: list[asyncio.Task[None]] = []
        self.conectado = False
        if self._almacen.leer_estado("instalado") is None:
            self._almacen.fijar_estado("instalado", ahora_iso())  # los dictados de antes no se procesan solos

    # ------------------------------------------------------------ ciclo de vida

    def arrancar(self, escuchar_voz: bool = True) -> None:
        for fila in self._almacen.extracciones_inacabadas():  # las que dejó a medias una ejecución anterior
            self._almacen.actualizar_extraccion(fila["id"], status="en_cola", waiting=None)  # la GPU se vuelve a mirar
            self._cola.put_nowait(fila["id"])
        self._tareas_async.append(asyncio.create_task(self._trabajar(), name="extracciones"))
        if escuchar_voz:
            self._tareas_async.append(asyncio.create_task(self._consumir(), name="voice-core"))

    async def parar(self) -> None:
        for t in self._tareas_async:
            t.cancel()
        await asyncio.gather(*self._tareas_async, return_exceptions=True)
        self._tareas_async.clear()

    # ------------------------------------------------------------ salidas

    @staticmethod
    def tarea_out(fila: dict) -> TaskOut:
        return TaskOut.model_validate(fila)

    @staticmethod
    def extraccion_out(fila: dict) -> ExtractionOut:
        return ExtractionOut.model_validate(fila)

    def _publicar_tarea(self, fila: dict, creada: bool) -> TaskOut:
        tarea = self.tarea_out(fila)
        clase = TaskCreated if creada else TaskUpdated
        self._bus.publicar(clase(seq=None, ts=ahora(), session_id=tarea.session_id, task=tarea))
        return tarea

    def _publicar_extraccion(self, fila: dict) -> ExtractionOut:
        ex = self.extraccion_out(fila)
        self._bus.publicar(ExtractionUpdated(seq=None, ts=ahora(), session_id=ex.session_id, extraction=ex))
        return ex

    def categorias_out(self) -> list[CategoryOut]:
        recuento = self._almacen.recuento_por_categoria()
        salida = [CategoryOut(name=n, description=d, pending=recuento.get(n, {}).get("pendiente", 0),
                              proposed=recuento.get(n, {}).get("propuesta", 0), configured=True)
                  for n, d in self.categorias.items()]
        for nombre, n in recuento.items():  # categorías quitadas de la configuración que aún tienen tareas
            if nombre not in self.categorias:
                salida.append(CategoryOut(name=nombre, description="", pending=n["pendiente"],
                                          proposed=n["propuesta"], configured=False))
        return salida

    def _categoria(self, nombre: str) -> str:
        for c in self.categorias:
            if c.casefold() == nombre.strip().casefold():
                return c
        raise CategoriaDesconocida(f"Categoría desconocida: «{nombre}» (hay {', '.join(self.categorias)})")

    # ------------------------------------------------------------ tareas a mano

    def crear_tarea(self, t: TaskIn) -> TaskOut:
        fila = self._almacen.crear_tarea(t.title.strip(), self._categoria(t.category), "pendiente",
                                         due=t.due.isoformat() if t.due else None, notas=t.notes)
        return self._publicar_tarea(fila, creada=True)

    def editar_tarea(self, id_: int, cambios: TaskPatch) -> TaskOut:
        if self._almacen.tarea(id_) is None:
            raise TareaNoEncontrada(id_)
        datos = cambios.model_dump(exclude_unset=True)
        if "category" in datos and datos["category"] is not None:
            datos["category"] = self._categoria(datos["category"])
        if datos.get("title"):
            datos["title"] = datos["title"].strip()
        if isinstance(datos.get("due"), date):
            datos["due"] = datos["due"].isoformat()
        datos = {k: v for k, v in datos.items() if v is not None or k in ("due", "notes")}
        fila = self._almacen.actualizar_tarea(id_, datos)
        return self._publicar_tarea(fila, creada=False)  # type: ignore[arg-type]

    # ------------------------------------------------------------ extracciones

    async def pedir_extraccion(self, sesion: str) -> ExtractionOut:
        """«Extraer tareas» desde el editor: la sesión debe existir en voice-core y estar terminada."""
        s = await asyncio.to_thread(self._voz.sesion, sesion)
        if s.get("status") != "final":
            raise SesionNoTerminada("El dictado sigue en curso: extrae las tareas cuando termine")
        return self._encolar(sesion, "manual")

    def _encolar(self, sesion: str, origen: str) -> ExtractionOut:
        fila = self._almacen.crear_extraccion(sesion, origen)
        self._cola.put_nowait(fila["id"])
        log.info("Extracción %d en cola (%s, sesión %s)", fila["id"], origen, sesion)
        return self._publicar_extraccion(fila)

    async def _trabajar(self) -> None:
        while True:
            id_ = await self._cola.get()
            try:
                await self.procesar(id_)
            except Exception:  # noqa: BLE001  # un fallo inesperado no debe parar la cola
                log.exception("Extracción %d: fallo inesperado", id_)
                self._publicar_extraccion(self._almacen.actualizar_extraccion(
                    id_, status="error", error="Fallo inesperado; ver datos\\tasks-service.log", waiting=None))
            if self._cola.empty():
                await self._liberar_modelo()

    async def _esperar_gpu(self, id_: int) -> None:
        """Con la GPU de LM Studio ocupada por otro programa, la extracción sigue en cola con el motivo y se vuelve a
        mirar cada `reintento_gpu_s`. Si el modelo ya está cargado no se reserva nada más y no hay que esperar."""
        if self._gpu is None:
            return
        esperando = False
        while not await asyncio.to_thread(self._llm.cargado):
            motivo = await asyncio.to_thread(self._gpu.motivo)
            if motivo is None:
                if esperando:
                    log.info("Extracción %d: la GPU ya está libre", id_)
                return
            if not esperando:
                esperando = True
                log.info("Extracción %d en espera: %s", id_, motivo)
                self._publicar_extraccion(self._almacen.actualizar_extraccion(id_, waiting=motivo))
            await asyncio.sleep(self._aj.reintento_gpu_s)

    async def _liberar_modelo(self) -> None:
        """Cola vacía: si el modelo lo cargó el organizador, se descarga ya (el `ttl` es solo la red de seguridad).
        Un modelo que ya estaba cargado antes (lo cargaste tú en LM Studio) no se toca."""
        if not (self._modelo_nuestro and self._aj.descargar_al_terminar):
            return
        self._modelo_nuestro = False
        try:
            n = await asyncio.to_thread(self._llm.descargar)
            log.info("Cola vacía: modelo descargado de LM Studio (%d instancia%s)", n, "" if n == 1 else "s")
        except ErrorLlm as e:
            log.warning("No se pudo descargar el modelo; lo hará el TTL de %d s: %s", self._llm.ttl_s, e)

    async def procesar(self, id_: int) -> ExtractionOut:
        await self._esperar_gpu(id_)
        fila = self._almacen.actualizar_extraccion(id_, status="procesando", model=self._llm.modelo, error=None,
                                                   waiting=None)
        self._publicar_extraccion(fila)
        sesion = fila["session_id"]
        try:
            info = await asyncio.to_thread(self._voz.sesion, sesion)
            texto = await asyncio.to_thread(self._voz.texto, sesion)
            propuestas, segundos = [], None
            if texto.strip():
                listo = await asyncio.to_thread(
                    self._llm.asegurar_servidor if self._aj.arrancar_lmstudio else self._llm.responde)
                if not listo:
                    raise ErrorLlm(f"LM Studio no responde en {self._llm.url}: ábrelo o arranca su servidor")
                if not await asyncio.to_thread(self._llm.cargado):
                    self._modelo_nuestro = True  # lo carga esta petición
                nombres = list(self.categorias)
                r = await asyncio.to_thread(self._llm.completar,
                                            mensajes(texto, self.categorias, _fecha_local(info["started_at"])),
                                            esquema(nombres))
                propuestas, segundos = interpretar(r.contenido, nombres), round(r.segundos, 2)
        except (ErrorLlm, RespuestaInvalida, VoiceCoreNoResponde, SesionDesconocida) as e:
            log.warning("Extracción %d (sesión %s): %s", id_, sesion, e)
            return self._publicar_extraccion(self._almacen.actualizar_extraccion(id_, status="error", error=str(e)))
        for vieja in self._almacen.propuestas_de(sesion):  # repetir la extracción sustituye las propuestas
            self._publicar_tarea(self._almacen.actualizar_tarea(vieja["id"], {"status": "descartada"}), creada=False)  # type: ignore[arg-type]
        for p in propuestas:
            nueva = self._almacen.crear_tarea(p.title, p.category, "propuesta", due=p.due, notas=p.notes,
                                              sesion=sesion, extraccion=id_, cita=p.quote)
            self._publicar_tarea(nueva, creada=True)
        log.info("Extracción %d: %d tareas en %s s", id_, len(propuestas), segundos)
        return self._publicar_extraccion(self._almacen.actualizar_extraccion(
            id_, status="lista", tasks=len(propuestas), duration_s=segundos))

    # ------------------------------------------------------------ consumidor de voice-core

    async def _consumir(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        while True:
            despues = int(self._almacen.leer_estado("voice_seq") or 0)
            try:
                async with connect(self._voz.url_ws(despues), open_timeout=3, max_size=None) as ws:
                    self.conectado = True
                    log.info("Conectado a voice-core (eventos desde el %d)", despues)
                    async for crudo in ws:
                        await self.al_evento_voz(json.loads(crudo))
            except (OSError, TimeoutError, WebSocketException) as e:
                log.debug("voice-core: %s", e)
            finally:
                if self.conectado:
                    log.info("Desconectado de voice-core; se reintenta")
                self.conectado = False
            await asyncio.sleep(REINTENTO_WS_S)

    async def al_evento_voz(self, ev: dict) -> None:
        tipo = ev.get("type")
        if tipo == "session.started":
            self._modos[ev["session_id"]] = ev.get("mode", "dictado")
        elif tipo == "session.finalized":
            await self._al_terminar_dictado(ev)
        seq = ev.get("seq")
        if seq is not None and seq > int(self._almacen.leer_estado("voice_seq") or 0):  # la marca solo avanza
            self._almacen.fijar_estado("voice_seq", str(seq))

    async def _al_terminar_dictado(self, ev: dict) -> None:
        sesion = ev["session_id"]
        modo = self._modos.pop(sesion, None)
        instalado = self._almacen.leer_estado("instalado")
        if instalado and datetime.fromisoformat(ev["ts"]) < datetime.fromisoformat(instalado):
            return  # dictado anterior a la instalación del organizador
        if not ev.get("text", "").strip() or self._almacen.hay_extraccion_auto(sesion):
            return
        if modo is None:  # empezó antes de conectarnos: se pregunta
            try:
                modo = (await asyncio.to_thread(self._voz.sesion, sesion)).get("mode")
            except (VoiceCoreNoResponde, SesionDesconocida) as e:
                log.warning("No se pudo saber el modo de la sesión %s: %s", sesion, e)
                return
        if modo == "tarea":
            self._encolar(sesion, "auto")


def _fecha_local(iso: str) -> date:
    """Día del dictado en la hora local (las fechas relativas se resuelven respecto a él)."""
    return datetime.fromisoformat(iso).astimezone().date()
