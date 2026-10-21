"""API REST y WebSocket de voice-core. Los modelos de entrada y salida están en `contratos.py`."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, PlainTextResponse

from voice_core import __version__
from voice_core.ajustes import Ajustes
from voice_core.almacen import Almacen
from voice_core.bus import Bus, ahora
from voice_core.contratos import (EngineModeIn, EngineStatusOut, ErrorOut, EstadoSesion, Evento, HealthOut,
                                  SessionDetailOut, SessionOut, SessionStartIn, ShutdownOut)
from voice_core.motor import Motor, MotorOcupado, MotorPausado, SinEspacioGpu
from voice_core.sesiones import GestorSesiones, SesionEnCurso, SesionNoActiva, SesionNoEncontrada

log = logging.getLogger(__name__)

CONFLICTOS = (SesionEnCurso, SesionNoActiva, MotorPausado, MotorOcupado, SinEspacioGpu)
RESPUESTAS_409 = {409: {"model": ErrorOut}}
RESPUESTAS_404 = {404: {"model": ErrorOut}}


@dataclass
class Servicios:
    aj: Ajustes
    almacen: Almacen
    bus: Bus
    motor: Motor
    sesiones: GestorSesiones
    al_arrancar: Callable[[asyncio.AbstractEventLoop], None] | None = None
    al_parar: Callable[[], None] | None = None
    al_pedir_cierre: Callable[[], None] | None = None


def crear_app(sv: Servicios) -> FastAPI:
    @asynccontextmanager
    async def ciclo(app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        sv.motor.vincular(loop)
        sv.sesiones.vincular(loop)
        cerradas = sv.almacen.cerrar_colgadas(ahora().isoformat())
        if cerradas:
            log.warning("%d sesiones colgadas de una ejecución anterior pasaron a final", cerradas)
        vigilancia = asyncio.create_task(sv.motor.vigilar())
        if sv.al_arrancar:
            sv.al_arrancar(loop)
        yield
        sv.bus.cerrar()
        if sv.al_parar:
            sv.al_parar()
        vigilancia.cancel()
        await sv.sesiones.cerrar()
        await sv.motor.apagar("servicio detenido", forzar=True)

    app = FastAPI(title="voice-core", version=__version__, lifespan=ciclo,
                  description="Transcripción local en tiempo real. Eventos en /ws y /events.")

    @app.exception_handler(SesionNoEncontrada)
    async def _404(request: Request, exc: SesionNoEncontrada) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Sesión no encontrada: {exc}"})

    for clase in CONFLICTOS:
        @app.exception_handler(clase)
        async def _409(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=409, content={"detail": str(exc)})

    def sesion_o_404(id_: str) -> dict:
        s = sv.almacen.sesion(id_)
        if s is None:
            raise SesionNoEncontrada(id_)
        return s

    # ------------------------------------------------------------ salud y motor

    @app.get("/health", response_model=HealthOut)
    def salud() -> HealthOut:
        return HealthOut(version=__version__)

    @app.get("/engine", response_model=EngineStatusOut)
    def motor() -> EngineStatusOut:
        return sv.motor.estado_out()

    @app.put("/engine/mode", response_model=EngineStatusOut)
    async def cambiar_modo(cuerpo: EngineModeIn) -> EngineStatusOut:
        await sv.motor.cambiar_modo(cuerpo.mode)
        return sv.motor.estado_out()

    @app.post("/engine/release", response_model=EngineStatusOut, responses=RESPUESTAS_409)
    async def liberar(stop_session: bool = Query(False, description="Si hay un dictado, lo termina antes de liberar")
                      ) -> EngineStatusOut:
        if stop_session:
            await sv.sesiones.liberar("liberado por petición")
        else:
            await sv.motor.apagar("liberado por petición")
        return sv.motor.estado_out()

    @app.post("/shutdown", response_model=ShutdownOut, status_code=status.HTTP_202_ACCEPTED,
              responses={409: {"model": ErrorOut}})
    async def cerrar_servicio() -> ShutdownOut:
        """Cierre ordenado: termina el dictado, cierra el worker (libera la VRAM) y para el servidor."""
        if sv.al_pedir_cierre is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Este servicio no admite cierre remoto")
        sv.bus.cerrar()  # suelta los WebSocket abiertos: si no, el servidor no termina de apagarse
        asyncio.get_running_loop().call_later(0.1, sv.al_pedir_cierre)  # deja salir la respuesta primero
        return ShutdownOut()

    # ------------------------------------------------------------ sesiones

    @app.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED, responses=RESPUESTAS_409)
    async def iniciar(cuerpo: SessionStartIn) -> SessionOut:
        return SessionOut.model_validate(await sv.sesiones.iniciar(cuerpo.mode))

    @app.post("/sessions/toggle", response_model=SessionOut, responses=RESPUESTAS_409)
    async def alternar(cuerpo: SessionStartIn) -> SessionOut:
        return SessionOut.model_validate(await sv.sesiones.alternar(cuerpo.mode))

    @app.post("/sessions/{session_id}/stop", response_model=SessionOut, responses={**RESPUESTAS_404, **RESPUESTAS_409})
    async def detener(session_id: str) -> SessionOut:
        sesion_o_404(session_id)
        return SessionOut.model_validate(await sv.sesiones.detener(session_id))

    @app.get("/sessions", response_model=list[SessionOut])
    def listar(limit: int = Query(20, ge=1, le=500), status_: EstadoSesion | None = Query(None, alias="status")
               ) -> list[SessionOut]:
        return [SessionOut.model_validate(s) for s in sv.almacen.listar_sesiones(limit, status_)]

    @app.get("/sessions/{session_id}", response_model=SessionDetailOut, responses=RESPUESTAS_404)
    def detalle(session_id: str) -> SessionDetailOut:
        s = sesion_o_404(session_id)
        return SessionDetailOut.model_validate({**s, "segments": sv.almacen.segmentos(session_id)})

    @app.get("/sessions/{session_id}/export", response_class=PlainTextResponse, responses=RESPUESTAS_404)
    def exportar(session_id: str, format_: Literal["md", "txt"] = Query("md", alias="format")) -> PlainTextResponse:
        texto = sv.sesiones.exportar(session_id, format_)
        tipo = "text/markdown; charset=utf-8" if format_ == "md" else "text/plain; charset=utf-8"
        return PlainTextResponse(texto, media_type=tipo)

    # ------------------------------------------------------------ eventos

    @app.get("/events", response_model=list[Evento])
    def eventos(after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)) -> list[Evento]:
        return sv.bus.desde(after, limit)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, after: int | None = Query(None, ge=0)) -> None:
        await websocket.accept()
        cola = sv.bus.suscribir()  # primero suscribirse y luego reenviar: así no hay hueco entre ambos
        try:
            ultimo = -1
            if after is not None:
                while lote := sv.bus.desde(max(after, ultimo), 1000):
                    for ev in lote:
                        await websocket.send_text(ev.model_dump_json())
                        ultimo = ev.seq or ultimo
            while True:
                ev = await cola.get()
                if ev is None:
                    if sv.bus.cerrando:
                        await websocket.close(code=1001, reason="voice-core se está cerrando")
                    else:
                        await websocket.close(code=1013, reason="cliente demasiado lento")
                    return
                if ev.seq is not None and ev.seq <= ultimo:
                    continue
                await websocket.send_text(ev.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            sv.bus.desuscribir(cola)

    return app
