"""Gestor de sesiones: une micrófono, motor, almacén y bus. Una sola sesión activa a la vez."""
from __future__ import annotations

import asyncio
import logging
import struct
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from voice_core import documento
from voice_core.ajustes import Ajustes
from voice_core.almacen import Almacen, Fila
from voice_core.audio import SR, Entrega, Fuente
from voice_core.bus import Bus, ahora
from voice_core.contratos import (AttachmentAdded, AttachmentOut, DocumentOut, EngineError, FormatoExport, ModoSesion,
                                  SegmentFinal, SegmentPartial, SessionEdited, SessionFinalized, SessionStarted)
from voice_core.motor import Motor

log = logging.getLogger(__name__)

FabricaFuente = Callable[[Entrega], Fuente]
Sonido = Callable[[str], None]
FIRMA_PNG = b"\x89PNG\r\n\x1a\n"
MAX_ADJUNTO = 25 * 1024 * 1024


class SesionEnCurso(Exception):
    pass


class SesionNoActiva(Exception):
    pass


class SesionNoEncontrada(Exception):
    pass


class AdjuntoNoEncontrado(Exception):
    pass


class AdjuntoInvalido(Exception):
    pass


class AdjuntoDemasiadoGrande(Exception):
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
        self._escribir_documento(a.id)
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

    # ------------------------------------------------------------ capturas y documento

    def carpeta(self, id_: str) -> Path:
        return self._aj.carpeta_datos / "sesiones" / id_

    def _sesion(self, id_: str) -> Fila:
        s = self._almacen.sesion(id_)
        if s is None:
            raise SesionNoEncontrada(id_)
        return s

    def _t_actual(self, s: Fila) -> float:
        """Segundos desde el inicio: ahora mismo si la sesión está grabando; si ya terminó, su final."""
        a = self._activa
        if a is not None and a.id == s["id"]:
            return time.monotonic() - a.t_inicio
        if s["ended_at"]:
            return max(0.0, (datetime.fromisoformat(s["ended_at"]) -
                             datetime.fromisoformat(s["started_at"])).total_seconds())
        return 0.0

    def agregar_adjunto(self, id_: str, png: bytes, t: float | None = None) -> AttachmentOut:
        s = self._sesion(id_)
        if len(png) > MAX_ADJUNTO:
            raise AdjuntoDemasiadoGrande(f"La imagen pesa {len(png) / 2**20:.1f} MB (máximo 25 MB)")
        if not png.startswith(FIRMA_PNG) or len(png) < 24:
            raise AdjuntoInvalido("Solo se aceptan imágenes PNG")
        ancho, alto = struct.unpack(">II", png[16:24])  # cabecera IHDR
        carpeta = self.carpeta(id_) / "capturas"
        carpeta.mkdir(parents=True, exist_ok=True)
        n = len(self._almacen.adjuntos(id_)) + 1
        while (carpeta / f"{n:03d}.png").exists():
            n += 1
        archivo = f"capturas/{n:03d}.png"
        (carpeta / f"{n:03d}.png").write_bytes(png)
        fila = self._almacen.agregar_adjunto(id_, self._t_actual(s) if t is None else t, archivo, ancho or None,
                                             alto or None, _iso(ahora()))
        if s["document"]:  # documento ya editado: la captura nueva se añade al final para no perderla
            md = documento.numerar_imagenes(f"{s['document'].rstrip()}\n\n![Imagen]({archivo})\n")
            self._almacen.guardar_documento(id_, md, s["edited_at"])
        if s["status"] == "final":
            self._escribir_documento(id_)
        adjunto = self.adjunto_out(fila)
        self._bus.publicar(AttachmentAdded(seq=None, ts=ahora(), session_id=id_, attachment=adjunto))
        log.info("Captura %s añadida a la sesión %s (t = %.1f s, %dx%d)", archivo, id_, fila["t"], ancho, alto)
        return adjunto

    def adjunto_out(self, fila: Fila) -> AttachmentOut:
        sid = fila["session_id"]
        return AttachmentOut(id=fila["id"], session_id=sid, kind=fila["kind"], t=round(fila["t"], 2), file=fila["file"],
                             path=str(self.carpeta(sid) / fila["file"]),
                             url=f"/sessions/{sid}/attachments/{fila['id']}", width=fila["width"],
                             height=fila["height"], created_at=fila["created_at"])

    def ruta_adjunto(self, id_: str, adjunto: int) -> Path:
        self._sesion(id_)
        fila = self._almacen.adjunto(id_, adjunto)
        if fila is None or not (self.carpeta(id_) / fila["file"]).is_file():
            raise AdjuntoNoEncontrado(f"{id_}/{adjunto}")
        return self.carpeta(id_) / fila["file"]

    def _titulo(self, s: Fila) -> str:
        inicio = datetime.fromisoformat(s["started_at"]).astimezone()
        return f"{'Tarea' if s['mode'] == 'tarea' else 'Dictado'} · {inicio:%Y-%m-%d %H:%M}"

    def documento(self, id_: str) -> DocumentOut:
        s = self._sesion(id_)
        markdown = s["document"] or documento.generar(self._titulo(s), self._almacen.segmentos(id_),
                                                       self._almacen.adjuntos(id_))
        return DocumentOut(session_id=id_, markdown=markdown, edited=s["document"] is not None,
                           edited_at=s["edited_at"], folder=str(self.carpeta(id_)),
                           images=len(documento.imagenes(markdown)))

    def guardar_documento(self, id_: str, markdown: str) -> DocumentOut:
        s = self._sesion(id_)
        if s["status"] != "final":
            raise SesionEnCurso("La sesión todavía está grabando; se podrá editar al terminar")
        editado = ahora()
        md = documento.numerar_imagenes(documento.unir_lineas(markdown))
        self._almacen.guardar_documento(id_, md, _iso(editado))
        self._escribir_documento(id_)
        self._bus.publicar(SessionEdited(seq=None, ts=editado, session_id=id_, edited_at=editado))
        return self.documento(id_)

    def borrar_documento(self, id_: str) -> DocumentOut:
        self._sesion(id_)
        self._almacen.guardar_documento(id_, None, None)
        self._escribir_documento(id_)
        self._bus.publicar(SessionEdited(seq=None, ts=ahora(), session_id=id_, edited_at=None))
        return self.documento(id_)

    def _escribir_documento(self, id_: str) -> None:
        """datos/sesiones/<id>/documento.md con rutas relativas: la carpeta entera se puede entregar a un agente."""
        try:
            d = self.documento(id_)
            ruta = self.carpeta(id_) / "documento.md"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(d.markdown, encoding="utf-8")
        except OSError as e:
            log.warning("No se pudo escribir documento.md de %s: %s", id_, e)

    # ------------------------------------------------------------ exportar

    def exportar(self, id_: str, formato: FormatoExport) -> str:
        d = self.documento(id_)
        if formato == "md":
            return documento.con_rutas_absolutas(d.markdown, Path(d.folder))
        if formato == "chat":
            return documento.para_chat(d.markdown)
        return documento.solo_texto(d.markdown)
