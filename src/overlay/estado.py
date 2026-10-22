"""Estado del panel: traduce los eventos de voice-core en lo que el panel muestra y hace. Sin Qt, para poder probarlo.

`aplicar(evento)` actualiza el estado y devuelve acciones para la interfaz:
  mostrar · copiar (texto de la sesión recién terminada) · copiar_chat (pedir el texto con [Imagen N] y copiarlo) ·
  actualizar_texto (la última sesión se editó) · programar_ocultar · cancelar_ocultar · consultar_motor
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Accion = Literal["mostrar", "copiar", "copiar_chat", "actualizar_texto", "programar_ocultar", "cancelar_ocultar",
                 "consultar_motor"]

COLOR_FINAL = "#F3F4F6"
COLOR_PARCIAL = "#9CA3AF"
COLOR_CAPTURA = "#A78BFA"
MARCA_CAPTURA = "\x00"  # prefijo de los marcadores de captura dentro de `finales`
COLORES_PUNTO = {"dictando": "#EF4444", "arrancando": "#F59E0B", "en_espera": "#60A5FA", "en_reposo": "#A78BFA",
                 "apagado": "#6B7280"}
NOMBRE_DISPOSITIVO = {"cuda": "5080", "cpu": "CPU"}


def _mmss(segundos: float) -> str:
    s = max(0, int(segundos))
    return f"{s // 60}:{s % 60:02d}"


@dataclass
class EstadoPanel:
    copiar_al_terminar: bool = True
    tecla_pegar: str = "Ctrl+Alt+V"
    conectado: bool = False
    motor: str = "apagado"
    dispositivo: str | None = None
    modo: str = "auto"
    sesion_id: str | None = None
    modo_sesion: str = "dictado"
    inicio: datetime | None = None
    fin: datetime | None = None  # micrófono cerrado (session.stopped): el reloj se para aquí
    grabando: bool = False  # sesión abierta: de session.started a session.finalized
    terminando: bool = False  # micrófono cerrado, falta el texto final (p. ej. el modelo aún carga)
    finales: list[str] = field(default_factory=list)
    parcial: str = ""
    capturas: int = 0
    ultimo_texto: str = ""
    aviso: str = ""
    aviso_error: bool = False
    _apaga_en: float | None = None  # instante (reloj monotónico) del apagado completo por inactividad
    _reposo_en: float | None = None  # instante en que el modelo pasa a RAM

    # ------------------------------------------------------------ eventos

    def aplicar(self, ev: dict) -> list[Accion]:
        tipo = ev.get("type")
        if tipo == "engine.state":
            self.motor, self.modo, self.dispositivo = ev["state"], ev["mode"], ev.get("device")
            self._apaga_en = self._reposo_en = None
            return ["consultar_motor"] if self.motor in ("en_espera", "en_reposo") else []
        if tipo == "session.started":
            self.sesion_id, self.modo_sesion = ev["session_id"], ev["mode"]
            self.inicio, self.fin = datetime.fromisoformat(ev["ts"]), None
            self.grabando, self.terminando, self.finales, self.parcial, self.capturas = True, False, [], "", 0
            self._avisar("")
            return ["mostrar", "cancelar_ocultar"]
        if tipo == "session.stopped" and ev.get("session_id") == self.sesion_id and self.grabando:
            self.terminando, self.fin = True, datetime.fromisoformat(ev["ts"])
            return []
        if tipo == "attachment.added" and ev.get("session_id") == self.sesion_id:
            self.capturas += 1
            if self.grabando:
                self.finales.append(f"{MARCA_CAPTURA}{self.capturas}")
                return []
            self._avisar(f"Captura {self.capturas} añadida al último dictado · ábrela en el editor (✎)")
            return ["mostrar", "programar_ocultar"]
        if tipo == "session.edited" and ev.get("session_id") == self.sesion_id and not self.grabando:
            return ["actualizar_texto"]
        if tipo == "segment.partial" and ev.get("session_id") == self.sesion_id and self.grabando:
            self.parcial = ev["text"]
            return []
        if tipo == "segment.final" and ev.get("session_id") == self.sesion_id:
            self.finales.append(ev["text"])
            self.parcial = ""
            return []
        if tipo == "session.finalized" and ev.get("session_id") == self.sesion_id:
            self.grabando, self.terminando, self.parcial = False, False, ""
            texto = ev.get("text", "").strip()
            acciones: list[Accion] = []
            if texto:
                self.ultimo_texto = texto
                self.finales = [texto]
                if self.copiar_al_terminar:
                    # Con capturas se copia la versión para chat ([Imagen N]); la pide el controlador al servicio.
                    acciones.append("copiar_chat" if self.capturas else "copiar")
                    self._avisar(f"Copiado · {self.tecla_pegar} para pegarlo donde esté el cursor")
                else:
                    self._avisar(f"Listo · {self.tecla_pegar} para pegarlo")
                if self.capturas:
                    plural = "captura" if self.capturas == 1 else "capturas"
                    self.aviso += f" · {self.capturas} {plural}: arrástralas desde el editor (✎)"
            else:
                self._avisar("No se reconoció voz en esta sesión")
            return [*acciones, "programar_ocultar"]
        if tipo == "engine.error":
            self._avisar(ev.get("message", "Error del motor"), error=True)
            return ["mostrar"]
        return []

    def fijar_conexion(self, conectado: bool) -> None:
        self.conectado = conectado
        if not conectado:
            self._avisar("voice-core no responde: reintentando…", error=True)
        elif self.aviso_error and self.aviso.startswith("voice-core"):
            self._avisar("")

    def fijar_apagado_en(self, segundos: float | None, ahora: float) -> None:
        self._apaga_en = ahora + segundos if segundos is not None else None

    def fijar_motor(self, motor: dict, ahora: float) -> None:
        """Estado leído con GET /engine (al conectar o al pasar a en_espera/en_reposo, para las cuentas atrás)."""
        self.motor, self.modo, self.dispositivo = motor["state"], motor["mode"], motor.get("device")
        self.fijar_apagado_en(motor.get("auto_off_in_s"), ahora)
        reposo = motor.get("rest_in_s")
        self._reposo_en = ahora + reposo if reposo is not None else None

    def cargar_ultimo(self, texto: str, sesion_id: str | None = None) -> None:
        """Al arrancar el overlay, el último dictado guardado queda listo para Ctrl+Alt+O, Ctrl+Alt+V y capturas."""
        if self.grabando:
            return
        if sesion_id:
            self.sesion_id = sesion_id
        if texto:
            self.ultimo_texto = texto
            self.finales = [texto]

    def _avisar(self, texto: str, error: bool = False) -> None:
        self.aviso, self.aviso_error = texto, error

    # ------------------------------------------------------------ lo que se dibuja

    @property
    def parrafo(self) -> str:
        return " ".join(f for f in self.finales if not f.startswith(MARCA_CAPTURA))

    @property
    def color_punto(self) -> str:
        if not self.conectado:
            return COLORES_PUNTO["apagado"]
        if self.terminando:
            return COLORES_PUNTO["arrancando"]
        return COLORES_PUNTO["dictando"] if self.grabando and self.motor != "arrancando" else \
            COLORES_PUNTO.get(self.motor, COLORES_PUNTO["apagado"])

    def html(self) -> str:
        trozos = []
        for f in self.finales:
            if f.startswith(MARCA_CAPTURA):
                trozos.append(f'<span style="color:{COLOR_CAPTURA}">[📷 {html.escape(f[1:])}]</span>')
            else:
                trozos.append(f'<span style="color:{COLOR_FINAL}">{html.escape(f)}</span>')
        cuerpo = " ".join(trozos)
        if self.parcial:
            separador = " " if self.finales else ""
            cuerpo += f'{separador}<span style="color:{COLOR_PARCIAL}; font-style:italic">{html.escape(self.parcial)}…</span>'
        if not self.finales and not self.parcial:
            if self.terminando:  # parado antes de tener texto: decir por qué aún no hay nada
                pista = ("Micrófono cerrado. Lo grabado se transcribe en cuanto termine de cargar el modelo…"
                         if self.motor == "arrancando" else "Micrófono cerrado. Transcribiendo lo grabado…")
            else:
                pista = "Escuchando… habla cuando quieras." if self.grabando else "Aún no hay dictados."
            cuerpo = f'<span style="color:{COLOR_PARCIAL}">{pista}</span>'
        return cuerpo

    def titulo(self, ahora_utc: datetime, ahora: float) -> str:
        if not self.conectado:
            return "voice-core sin conexión"
        disp = NOMBRE_DISPOSITIVO.get(self.dispositivo or "", "")
        if self.grabando:
            partes = [self.modo_sesion]
            if self.inicio is not None:
                partes.append(_mmss(((self.fin or ahora_utc) - self.inicio).total_seconds()))
            if self.terminando:
                partes.append("terminando de cargar el modelo…" if self.motor == "arrancando" else "transcribiendo…")
            else:
                partes.append("cargando el modelo…" if self.motor == "arrancando" else disp)
            return " · ".join(p for p in partes if p)
        if self.modo == "pausado":
            return "en pausa · no escucha"
        if self.motor == "en_espera":
            if self._reposo_en is not None:
                return f"listo en {disp} · pasa a RAM en {_mmss(self._reposo_en - ahora)}"
            if self._apaga_en is not None:
                return f"listo en {disp} · se apaga en {_mmss(self._apaga_en - ahora)}"
            return f"listo en {disp}"
        if self.motor == "en_reposo":
            sufijo = f" · se apaga en {_mmss(self._apaga_en - ahora)}" if self._apaga_en is not None else ""
            return f"en reposo (RAM) · arranca en <1 s{sufijo}"
        if self.motor == "arrancando":
            return "cargando el modelo…"
        return "motor apagado · GPU libre"
