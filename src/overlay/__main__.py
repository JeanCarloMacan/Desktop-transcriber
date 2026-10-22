"""Arranque del overlay: `pythonw -m overlay` (o `transcriptor`). Lanza voice-core oculto si hace falta.

El Controlador une el cliente de voice-core, el estado del panel, el panel, la bandeja, el editor, las capturas y los
atajos propios (mostrar/ocultar, pegar y capturar). Todo lo de Qt vive en el hilo principal; atajos y red llegan por
señales.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from overlay import escritorio
from overlay.bandeja import Bandeja
from overlay.capturas import Capturador
from overlay.cliente import Cliente
from overlay.editor import Editor
from overlay.estado import EstadoPanel
from overlay.panel import Panel
from overlay.servicio import ServicioLocal
from voice_core.ajustes import Ajustes, cargar, ruta_config_por_defecto
from voice_core.hotkeys import Hotkeys

log = logging.getLogger("overlay")


def bonita(combo: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in combo.split("+"))


class Controlador(QObject):
    pedir_mostrar = Signal()
    pedir_pegar = Signal()
    pedir_captura = Signal()

    def __init__(self, aj: Ajustes, servicio: ServicioLocal) -> None:
        super().__init__()
        self._aj = aj
        self._servicio = servicio
        o = aj.overlay
        self.estado = EstadoPanel(copiar_al_terminar=o.copiar_al_terminar, tecla_pegar=bonita(o.pegar))
        self.panel = Panel(o.ancho, o.letra, o.opacidad, aj.carpeta_datos / "overlay.json")
        self.bandeja = Bandeja(bonita(o.mostrar), bonita(o.pegar), bonita(aj.hotkeys.liberar), bonita(o.capturar),
                               servicio.propio)
        self.cliente = Cliente(f"http://{aj.servidor.host}:{aj.servidor.puerto}")
        self.editor = Editor(self.cliente)
        self.capturador = Capturador()
        self._a_mano = False  # mostrado con Ctrl+Alt+O o la bandeja: no se oculta solo
        self._raton_dentro = False
        self._sesion_mostrada: str | None = None
        self._ocultas: list[QObject] = []  # ventanas escondidas durante un recorte
        self._insertar_en_editor = False
        self._ocultar = QTimer(self, singleShot=True)
        self._ocultar.timeout.connect(self.panel.ocultar)
        self._reloj = QTimer(self, interval=1000)
        self._reloj.timeout.connect(self._tic)
        self._hotkeys = Hotkeys({o.mostrar: self.pedir_mostrar.emit, o.pegar: self.pedir_pegar.emit,
                                 o.capturar: self.pedir_captura.emit})

        self.cliente.evento.connect(self._evento)
        self.cliente.conexion.connect(self._conexion)
        self.cliente.respuesta.connect(self._respuesta)
        self.panel.accion.connect(self._accion)
        self.panel.raton.connect(self._raton)
        self.bandeja.accion.connect(self._accion)
        self.bandeja.modo.connect(lambda m: self.cliente.pedir("modo", "PUT", "/engine/mode", {"mode": m}))
        self.pedir_mostrar.connect(self._alternar_panel)
        self.pedir_pegar.connect(self._pegar)
        self.pedir_captura.connect(lambda: self._capturar(None))
        self.editor.capturar.connect(self._capturar)
        self.capturador.lista.connect(self._captura_lista)
        self.capturador.terminada.connect(self._restaurar_ventanas)

    def iniciar(self) -> None:
        self._dibujar()
        self.bandeja.show()
        self.cliente.iniciar()
        self._hotkeys.iniciar()
        self._reloj.start()
        o = self._aj.overlay
        if self._hotkeys.registradas != [o.mostrar, o.pegar, o.capturar]:
            self.bandeja.showMessage("Transcriptor RT", "Algún atajo del overlay está ocupado por otra aplicación; "
                                     "revisa config\\voice-core.toml.", QSystemTrayIcon.MessageIcon.Information, 5000)

    # ------------------------------------------------------------ eventos de voice-core

    def _evento(self, ev: dict) -> None:
        for accion in self.estado.aplicar(ev):
            if accion == "mostrar":
                self._a_mano = False
                self.panel.mostrar()
            elif accion == "cancelar_ocultar":
                self._ocultar.stop()
            elif accion == "copiar":
                QGuiApplication.clipboard().setText(self.estado.ultimo_texto)
            elif accion == "copiar_chat":
                self._pedir_texto_chat("texto_chat")
            elif accion == "actualizar_texto":
                self._pedir_texto_chat("texto_editado")
            elif accion == "programar_ocultar":
                self._programar_ocultar()
            elif accion == "consultar_motor":
                self.cliente.pedir("motor", "GET", "/engine")
        self.editor.al_evento(ev)
        self._dibujar()

    def _pedir_texto_chat(self, operacion: str) -> None:
        """Texto de la última sesión en versión chat ([Imagen N], ediciones incluidas)."""
        if self.estado.sesion_id:
            self.cliente.pedir(operacion, "GET", f"/sessions/{self.estado.sesion_id}/export?format=chat")

    def _conexion(self, ok: bool) -> None:
        self.estado.fijar_conexion(ok)
        if ok:
            self.cliente.pedir("motor", "GET", "/engine")
            if not self.estado.ultimo_texto:
                self.cliente.pedir("ultimo", "GET", "/sessions?limit=1&status=final")
        self._dibujar()

    def _respuesta(self, operacion: str, resultado: object) -> None:
        if isinstance(resultado, Exception):
            if operacion in ("liberar", "modo", "captura"):
                self.estado.aviso, self.estado.aviso_error = f"No se pudo: {resultado}", True
                self.panel.mostrar()
            if operacion == "captura":
                self.editor.captura_cancelada()
        elif operacion == "motor" and isinstance(resultado, dict):
            self.estado.fijar_motor(resultado, time.monotonic())
        elif operacion == "ultimo" and isinstance(resultado, list) and resultado:
            s = resultado[0]
            self.estado.cargar_ultimo(s.get("text", ""), s.get("id"))
            if s.get("attachments") or s.get("edited_at"):
                self._pedir_texto_chat("texto_editado")
        elif operacion in ("texto_chat", "texto_editado") and isinstance(resultado, str) and resultado.strip():
            self.estado.ultimo_texto = resultado.strip()
            if operacion == "texto_chat":
                QGuiApplication.clipboard().setText(self.estado.ultimo_texto)
        elif operacion == "captura" and isinstance(resultado, dict):
            if self._insertar_en_editor:
                self._insertar_en_editor = False
                self.editor.insertar_imagen(resultado)
        self._dibujar()

    # ------------------------------------------------------------ capturas

    def _capturar(self, sesion_editor: str | None) -> None:
        """Recorte de Windows → captura adjunta. Desde el editor, va a su sesión y al cursor; con el atajo, al
        dictado en curso en el instante de pulsar, o si no hay dictado, al último."""
        if self.capturador.esperando:
            return
        t: float | None = None
        if sesion_editor is not None:
            sesion = sesion_editor
        elif self.estado.grabando and self.estado.sesion_id and self.estado.inicio is not None:
            sesion = self.estado.sesion_id
            t = max(0.0, (datetime.now(UTC) - self.estado.inicio).total_seconds())
        else:
            sesion = self.estado.sesion_id
        if not sesion:
            self.estado.aviso, self.estado.aviso_error = "Todavía no hay ningún dictado al que añadir la captura", True
            self.panel.mostrar()
            self._dibujar()
            return
        self._insertar_en_editor = sesion_editor is not None
        self._ocultas = [v for v in (self.panel, self.editor) if v.isVisible()]
        for ventana in self._ocultas:
            ventana.hide()  # que no tapen lo que quieres recortar
        if not self.capturador.capturar({"sesion": sesion, "t": t}):
            self._restaurar_ventanas(con_imagen=False)

    def _captura_lista(self, png: bytes, contexto: dict) -> None:
        ruta = f"/sessions/{contexto['sesion']}/attachments"
        if contexto.get("t") is not None:
            ruta += f"?t={contexto['t']:.2f}"
        self.cliente.subir("captura", ruta, png, "image/png")

    def _restaurar_ventanas(self, con_imagen: bool) -> None:
        ocultas, self._ocultas = self._ocultas, []
        for ventana in ocultas:
            ventana.show()
        if self.editor in ocultas:
            self.editor.raise_()
            self.editor.activateWindow()
        if not con_imagen:  # recorte cancelado: nada que subir ni insertar
            self._insertar_en_editor = False
            self.editor.captura_cancelada()

    # ------------------------------------------------------------ acciones

    def _accion(self, nombre: str) -> None:
        if nombre == "copiar" and self.estado.ultimo_texto:
            QGuiApplication.clipboard().setText(self.estado.ultimo_texto)
            self.estado.aviso, self.estado.aviso_error = "Copiado al portapapeles", False
        elif nombre == "pegar":
            self._pegar()
        elif nombre == "liberar":
            self.cliente.pedir("liberar", "POST", "/engine/release?stop_session=true")
        elif nombre == "ocultar":
            self._a_mano = False
            self._ocultar.stop()
            self.panel.ocultar()
        elif nombre == "mostrar":
            self._alternar_panel()
        elif nombre == "editor":
            self.editor.abrir(self.estado.sesion_id)
        elif nombre == "capturar":
            self._capturar(None)
        elif nombre == "carpeta":
            os.startfile(self._aj.carpeta_datos)  # noqa: S606
        elif nombre == "log":
            os.startfile(self._aj.carpeta_datos / "voice-core.log")  # noqa: S606
        elif nombre == "salir":
            self.salir()
        self._dibujar()

    def _alternar_panel(self) -> None:
        if self.panel.isVisible():
            self._a_mano = False
            self.panel.ocultar()
        else:
            self._a_mano = True
            self._ocultar.stop()
            self._dibujar()
            self.panel.mostrar()

    def _pegar(self) -> None:
        texto = self.estado.ultimo_texto
        if not texto:
            self.estado.aviso, self.estado.aviso_error = "Todavía no hay ningún dictado que pegar", True
            self._dibujar()
            return
        QGuiApplication.clipboard().setText(texto)
        # Espera a que sueltes Ctrl/Alt y pulsa Ctrl+V; en otro hilo para no congelar el panel mientras tanto.
        threading.Thread(target=escritorio.enviar_ctrl_v, daemon=True, name="pegar").start()

    def _programar_ocultar(self) -> None:
        segundos = self._aj.overlay.visible_tras_s
        if self._a_mano or self._raton_dentro or segundos <= 0 or self.estado.grabando:
            return
        self._ocultar.start(int(segundos * 1000))

    def _raton(self, dentro: bool) -> None:
        self._raton_dentro = dentro
        if dentro:
            self._ocultar.stop()
        elif self.panel.isVisible() and not self.estado.grabando:
            self._programar_ocultar()

    # ------------------------------------------------------------ dibujo

    def _dibujar(self) -> None:
        ahora = time.monotonic()
        self.panel.actualizar(self.estado, ahora)
        if self.estado.sesion_id != self._sesion_mostrada:  # dictado nuevo: se muestra desde su final
            self._sesion_mostrada = self.estado.sesion_id
            self.panel.desplazar_al_final()
        self.bandeja.actualizar(self.estado, self.estado.titulo(datetime.now(UTC), ahora))

    def _tic(self) -> None:
        ahora = time.monotonic()
        if self.panel.isVisible():
            self.panel.refrescar_titulo(self.estado, ahora)
        self.bandeja.actualizar(self.estado, self.estado.titulo(datetime.now(UTC), ahora))

    def salir(self) -> None:
        log.info("Saliendo")
        self._reloj.stop()
        self._hotkeys.detener()
        self.cliente.detener()
        self.panel.hide()
        self.bandeja.hide()
        self._servicio.cerrar()
        QApplication.quit()


def main() -> int:
    ap = argparse.ArgumentParser(prog="transcriptor", description="Overlay y bandeja del transcriptor RT")
    ap.add_argument("--config", type=Path, default=None)
    a = ap.parse_args()
    ruta_config = (a.config or ruta_config_por_defecto()).resolve()
    if not ruta_config.is_file():  # lanzado desde otra carpeta: se busca la config junto al proyecto
        carpeta = Path(__file__).resolve().parents[2] / "config"
        ruta_config = next((p for p in (carpeta / "voice-core.toml", carpeta / "voice-core.example.toml")
                            if p.is_file()), ruta_config)
    aj = cargar(ruta_config)
    aj.carpeta_datos.mkdir(parents=True, exist_ok=True)
    salidas: list[logging.Handler] = [logging.FileHandler(aj.carpeta_datos / "overlay.log", encoding="utf-8")]
    if sys.stderr is not None:
        salidas.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=salidas)

    if not escritorio.instancia_unica("TranscriptorRT-overlay"):
        log.info("Ya hay un overlay en marcha: esta instancia se cierra")
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Transcriptor RT")
    servicio = ServicioLocal(aj, ruta_config)
    if not servicio.asegurar():
        log.error("voice-core no está disponible; el overlay seguirá intentando conectar")
    controlador = Controlador(aj, servicio)
    controlador.iniciar()
    codigo = app.exec()
    log.info("Overlay cerrado")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
