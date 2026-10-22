"""Contratos públicos de tasks-service: modelos de la API REST y eventos.

Fuente de verdad de `contracts/tareas.openapi.json` y `contracts/tareas.eventos.schema.json`
(los genera `scripts/exportar_contratos.py`; un test vigila que no se desincronicen).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

EstadoTarea = Literal["propuesta", "pendiente", "hecha", "descartada"]
EstadoExtraccion = Literal["en_cola", "procesando", "lista", "error"]
OrigenExtraccion = Literal["auto", "manual"]

Titulo = Annotated[str, Field(min_length=1, max_length=200)]
Notas = Annotated[str, Field(max_length=4000)]


# ---------------------------------------------------------------- REST: entradas

class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Titulo
    category: str = Field(min_length=1, max_length=80)
    due: date | None = None
    notes: Notas | None = None


class TaskPatch(BaseModel):
    """Solo cambian los campos presentes; `due` o `notes` a null los borran."""
    model_config = ConfigDict(extra="forbid")
    title: Titulo | None = None
    category: str | None = Field(None, min_length=1, max_length=80)
    due: date | None = None
    notes: Notas | None = None
    status: Literal["pendiente", "hecha", "descartada"] | None = Field(
        None, description="propuesta → pendiente es aprobarla; ninguna vuelve a propuesta")


class ExtractionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------- REST: salidas

class TaskOut(BaseModel):
    id: int
    title: str
    category: str
    due: date | None
    notes: str | None
    status: EstadoTarea
    session_id: str | None = Field(description="Dictado de voice-core del que salió; null si se creó a mano")
    extraction_id: int | None
    quote: str | None = Field(description="Fragmento del dictado que la originó")
    created_at: datetime
    updated_at: datetime
    done_at: datetime | None


class ExtractionOut(BaseModel):
    id: int
    session_id: str
    origin: OrigenExtraccion
    status: EstadoExtraccion
    model: str | None
    tasks: int = Field(ge=0, description="Tareas propuestas")
    duration_s: float | None = Field(description="Segundos de la llamada al modelo (carga incluida)")
    error: str | None
    waiting: str | None = Field(None, description="Motivo por el que sigue en cola (p. ej. la GPU de LM Studio está "
                                "ocupada por otro programa); null si no espera")
    created_at: datetime


class CategoryOut(BaseModel):
    name: str
    description: str
    pending: int = Field(ge=0)
    proposed: int = Field(ge=0)
    configured: bool = Field(description="false = ya no está en la configuración; sus tareas siguen aquí")


class LlmOut(BaseModel):
    url: str
    model: str
    reachable: bool
    loaded: bool | None = Field(description="null si LM Studio no responde")
    ttl_s: int


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    voice_core: bool = Field(description="Conectado al WebSocket de voice-core")


# ---------------------------------------------------------------- Eventos

class _EventoBase(BaseModel):
    seq: int | None = Field(description="Orden global de tasks-service")
    ts: datetime


class TaskCreated(_EventoBase):
    type: Literal["task.created"] = "task.created"
    session_id: str | None = None
    task: TaskOut


class TaskUpdated(_EventoBase):
    type: Literal["task.updated"] = "task.updated"
    session_id: str | None = None
    task: TaskOut


class ExtractionUpdated(_EventoBase):
    type: Literal["extraction.updated"] = "extraction.updated"
    session_id: str
    extraction: ExtractionOut


EventoTareas = Annotated[Union[TaskCreated, TaskUpdated, ExtractionUpdated], Field(discriminator="type")]
EVENTO_TAREAS = TypeAdapter(EventoTareas)
EVENTOS_EFIMEROS: set[str] = set()
