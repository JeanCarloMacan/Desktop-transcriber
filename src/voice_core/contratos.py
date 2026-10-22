"""Contratos públicos de voice-core: modelos de la API REST y eventos del bus.

Este módulo es la fuente de verdad. `scripts/exportar_contratos.py` genera a partir de él `contracts/openapi.json`
y `contracts/eventos.schema.json`, y un test comprueba que no se desincronizan.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ModoSesion = Literal["dictado", "tarea"]
EstadoSesion = Literal["grabando", "procesando", "final"]
EstadoMotor = Literal["apagado", "arrancando", "dictando", "en_espera", "en_reposo"]
ModoMotor = Literal["auto", "gpu", "cpu", "pausado"]
Dispositivo = Literal["cuda", "cpu"]


# ---------------------------------------------------------------- REST: entradas

class SessionStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ModoSesion = "dictado"


class EngineModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ModoMotor


# ---------------------------------------------------------------- REST: salidas

class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class EngineStatusOut(BaseModel):
    state: EstadoMotor
    mode: ModoMotor
    device: Dispositivo | None = Field(description="Dispositivo del worker vivo; null si está apagado")
    model: str
    idle_s: float | None = Field(description="Segundos sin uso en en_espera o en_reposo; null en otros estados")
    rest_in_s: float | None = Field(None, description="Segundos hasta pasar el modelo a RAM (en_reposo); null si no aplica")
    auto_off_in_s: float | None = Field(description="Segundos hasta el apagado completo por inactividad; null si no aplica")
    vram_used_mib: int | None = Field(description="VRAM usada en la 5080 (todos los procesos); null sin NVML")
    vram_free_mib: int | None


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    index: int = Field(ge=0)
    t0: float = Field(ge=0, description="Inicio en segundos desde el comienzo de la sesión")
    t1: float = Field(ge=0)
    text: str


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mode: ModoSesion
    status: EstadoSesion
    started_at: datetime
    ended_at: datetime | None
    text: str = Field(description="Transcripción tal cual; el texto corregido está en /document")
    device: Dispositivo | None
    model: str | None
    attachments: int = Field(0, ge=0, description="Número de capturas adjuntas")
    edited_at: datetime | None = Field(None, description="Última edición del documento; null si no se editó")


class AttachmentOut(BaseModel):
    id: int
    session_id: str
    kind: Literal["imagen"]
    t: float = Field(ge=0, description="Segundos desde el inicio de la sesión en que se tomó")
    file: str = Field(description="Ruta relativa a la carpeta de la sesión, p. ej. capturas/001.png")
    path: str = Field(description="Ruta absoluta en disco")
    url: str = Field(description="Ruta de la API para descargarla")
    width: int | None
    height: int | None
    created_at: datetime


class SessionDetailOut(SessionOut):
    segments: list[SegmentOut]
    audio_path: str | None
    attachment_items: list[AttachmentOut] = Field(default_factory=list, description="Capturas adjuntas, por tiempo")


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = Field(max_length=2_000_000)


class DocumentOut(BaseModel):
    session_id: str
    markdown: str = Field(description="Markdown con rutas de imagen relativas a `folder`")
    edited: bool
    edited_at: datetime | None
    folder: str = Field(description="Carpeta de la sesión: documento.md, capturas/ y audio.wav")
    images: int = Field(ge=0)


FormatoExport = Literal["md", "chat", "txt"]


class ErrorOut(BaseModel):
    detail: str


class ShutdownOut(BaseModel):
    status: Literal["cerrando"] = "cerrando"


# ---------------------------------------------------------------- Eventos

class _EventoBase(BaseModel):
    seq: int | None = Field(description="Orden global; null en eventos efímeros (segment.partial)")
    ts: datetime


class SessionStarted(_EventoBase):
    type: Literal["session.started"] = "session.started"
    session_id: str
    mode: ModoSesion


class SegmentPartial(_EventoBase):
    type: Literal["segment.partial"] = "segment.partial"
    session_id: str
    text: str = Field(description="Texto provisional de la frase en curso; lo reemplaza el siguiente parcial o final")


class SegmentFinal(_EventoBase):
    type: Literal["segment.final"] = "segment.final"
    session_id: str
    index: int = Field(ge=0)
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    text: str


class SessionFinalized(_EventoBase):
    type: Literal["session.finalized"] = "session.finalized"
    session_id: str
    text: str
    duration_s: float = Field(ge=0)
    segments: int = Field(ge=0)


class EngineState(_EventoBase):
    type: Literal["engine.state"] = "engine.state"
    state: EstadoMotor
    mode: ModoMotor
    device: Dispositivo | None
    reason: str


class EngineError(_EventoBase):
    type: Literal["engine.error"] = "engine.error"
    message: str
    session_id: str | None = None


class AttachmentAdded(_EventoBase):
    type: Literal["attachment.added"] = "attachment.added"
    session_id: str
    attachment: AttachmentOut


class SessionEdited(_EventoBase):
    type: Literal["session.edited"] = "session.edited"
    session_id: str
    edited_at: datetime | None = Field(description="null = se volvió al documento generado")


Evento = Annotated[
    Union[SessionStarted, SegmentPartial, SegmentFinal, SessionFinalized, EngineState, EngineError, AttachmentAdded,
          SessionEdited],
    Field(discriminator="type"),
]
EVENTO = TypeAdapter(Evento)
EVENTOS_EFIMEROS = {"segment.partial"}
