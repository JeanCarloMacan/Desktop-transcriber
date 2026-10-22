"""API REST y WebSocket de tasks-service. Los modelos de entrada y salida están en `contratos.py`."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from voice_core.bus import Bus
from voice_core.contratos import ErrorOut, ShutdownOut

from tasks_service import __version__
from tasks_service.almacen import AlmacenTareas
from tasks_service.contratos import (CategoryOut, EstadoTarea, EventoTareas, ExtractionIn, ExtractionOut, HealthOut,
                                     LlmOut, TaskIn, TaskOut, TaskPatch)
from tasks_service.organizador import CategoriaDesconocida, Organizador, SesionNoTerminada, TareaNoEncontrada
from tasks_service.voz import SesionDesconocida, VoiceCoreNoResponde

log = logging.getLogger(__name__)

RESPUESTAS_404 = {404: {"model": ErrorOut}}


@dataclass
class ServiciosTareas:
    almacen: AlmacenTareas
    bus: Bus
    organizador: Organizador
    llm: Any
    escuchar_voz: bool = True
    al_pedir_cierre: Callable[[], None] | None = None


def crear_app(sv: ServiciosTareas) -> FastAPI:
    @asynccontextmanager
    async def ciclo(app: FastAPI) -> AsyncIterator[None]:
        sv.organizador.arrancar(escuchar_voz=sv.escuchar_voz)
        yield
        sv.bus.cerrar()
        await sv.organizador.parar()

    app = FastAPI(title="tasks-service", version=__version__, lifespan=ciclo,
                  description="Tareas por categoría extraídas de los dictados de voice-core con un modelo local "
                              "de LM Studio. Eventos en /ws y /events.")

    def error(codigo: int) -> Callable[[Request, Exception], Any]:
        async def manejar(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=codigo, content={"detail": str(exc)})
        return manejar

    app.add_exception_handler(TareaNoEncontrada, error(404))
    app.add_exception_handler(SesionDesconocida, error(404))
    app.add_exception_handler(SesionNoTerminada, error(409))
    app.add_exception_handler(CategoriaDesconocida, error(422))
    app.add_exception_handler(VoiceCoreNoResponde, error(503))

    @app.get("/health", response_model=HealthOut)
    def salud() -> HealthOut:
        return HealthOut(version=__version__, voice_core=sv.organizador.conectado)

    @app.get("/llm", response_model=LlmOut)
    async def llm() -> LlmOut:
        cargado = await asyncio.to_thread(sv.llm.cargado)
        return LlmOut(url=sv.llm.url, model=sv.llm.modelo, reachable=cargado is not None, loaded=cargado,
                      ttl_s=sv.llm.ttl_s)

    @app.get("/categories", response_model=list[CategoryOut])
    def categorias() -> list[CategoryOut]:
        return sv.organizador.categorias_out()

    # ------------------------------------------------------------ tareas

    @app.get("/tasks", response_model=list[TaskOut])
    def tareas(status_: EstadoTarea | None = Query(None, alias="status"), category: str | None = None,
               limit: int = Query(500, ge=1, le=5000)) -> list[TaskOut]:
        return [sv.organizador.tarea_out(f) for f in sv.almacen.tareas(status_, category, limit)]

    @app.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED,
              responses={422: {"model": ErrorOut}})
    def crear(cuerpo: TaskIn) -> TaskOut:
        return sv.organizador.crear_tarea(cuerpo)

    @app.patch("/tasks/{task_id}", response_model=TaskOut, responses=RESPUESTAS_404)
    def editar(task_id: int, cuerpo: TaskPatch) -> TaskOut:
        return sv.organizador.editar_tarea(task_id, cuerpo)

    # ------------------------------------------------------------ extracciones

    @app.post("/extractions", response_model=ExtractionOut, status_code=status.HTTP_202_ACCEPTED,
              responses={**RESPUESTAS_404, 409: {"model": ErrorOut}, 503: {"model": ErrorOut}})
    async def extraer(cuerpo: ExtractionIn) -> ExtractionOut:
        """Encola la extracción de tareas de un dictado terminado; sustituye sus propuestas anteriores."""
        return await sv.organizador.pedir_extraccion(cuerpo.session_id)

    @app.get("/extractions", response_model=list[ExtractionOut])
    def extracciones(session_id: str | None = None, limit: int = Query(50, ge=1, le=500)) -> list[ExtractionOut]:
        return [sv.organizador.extraccion_out(f) for f in sv.almacen.extracciones(session_id, limit)]

    # ------------------------------------------------------------ eventos y cierre

    @app.get("/events", response_model=list[EventoTareas])
    def eventos(after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)) -> list[Any]:
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
                        await websocket.close(code=1001, reason="tasks-service se está cerrando")
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

    @app.post("/shutdown", response_model=ShutdownOut, status_code=status.HTTP_202_ACCEPTED,
              responses={409: {"model": ErrorOut}})
    async def cerrar_servicio() -> ShutdownOut:
        if sv.al_pedir_cierre is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Este servicio no admite cierre remoto")
        sv.bus.cerrar()
        asyncio.get_running_loop().call_later(0.1, sv.al_pedir_cierre)
        return ShutdownOut()

    return app
