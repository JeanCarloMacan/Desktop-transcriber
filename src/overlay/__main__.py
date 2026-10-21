"""Arranque del overlay: `pythonw -m overlay` (o `transcriptor`). Lanza voice-core oculto si hace falta.

El Controlador une el cliente de voice-core, el estado del panel, el panel, la bandeja y los atajos propios
(mostrar/ocultar y pegar). Todo lo de Qt vive en el hilo principal; atajos y red llegan por señales.
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
from overlay.cliente import Cliente
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

    def __init__(self, aj: Ajustes, servicio: ServicioLocal) -> None:
        super().__init__()
        self._aj = aj
        self._servicio = servicio
        o = aj.overlay
        self.estado = EstadoPanel(copiar_al_terminar=o.copiar_al_terminar, tecla_pegar=bonita(o.pegar))
        self.panel = Panel(o.ancho, o.letra, o.opacidad, aj.carpeta_datos / "overlay.json")
        self.bandeja = Bandeja(bonita(o.mostrar), bonita(o.pegar), bonita(aj.hotkeys.liberar), servicio.propio)
        self.cliente = Cliente(f"http://{aj.servidor.host}:{aj.servidor.puerto}")
        self._a_mano = False  # mostrado con Ctrl+Alt+O o la bandeja: no se oculta solo
        self._raton_dentro = False
        self._sesion_mostrada: str | None = None
        self._ocultar = QTimer(self, singleShot=True)
        self._ocultar.timeout.connect(self.panel.ocultar)
        self._reloj = QTimer(self, interval=1000)
        self._reloj.timeout.connect(self._tic)
        self._hotkeys = Hotkeys({o.mostrar: self.pedir_mostrar.emit, o.pegar: self.pedir_pegar.emit})

        self.cliente.evento.connect(self._evento)
        self.cliente.conexion.connect(self._conexion)
        self.cliente.respuesta.connect(self._respuesta)
        self.panel.accion.connect(self._accion)
        self.panel.raton.connect(self._raton)
        self.bandeja.accion.connect(self._accion)
        self.bandeja.modo.connect(lambda m: self.cliente.pedir("modo", "PUT", "/engine/mode", {"mode": m}))
        self.pedir_mostrar.connect(self._alternar_panel)
        self.pedir_pegar.connect(self._pegar)

    def iniciar(self) -> None:
        self._dibujar()
        self.bandeja.show()
        self.cliente.iniciar()
        self._hotkeys.iniciar()
        self._reloj.start()
        if self._hotkeys.registradas != [self._aj.overlay.mostrar, self._aj.overlay.pegar]:
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
            elif accion == "programar_ocultar":
                self._programar_ocultar()
            elif accion == "consultar_motor":
                self.cliente.pedir("motor", "GET", "/engine")
        self._dibujar()

    def _conexion(self, ok: bool) -> None:
        self.estado.fijar_conexion(ok)
        if ok:
            self.cliente.pedir("motor", "GET", "/engine")
            if not self.estado.ultimo_texto:
                self.cliente.pedir("ultimo", "GET", "/sessions?limit=1&status=final")
        self._dibujar()

    def _respuesta(self, operacion: str, resultado: object) -> None:
        if isinstance(resultado, Exception):
            if operacion in ("liberar", "modo"):
                self.estado.aviso, self.estado.aviso_error = f"No se pudo: {resultado}", True
                self.panel.mostrar()
        elif operacion == "motor" and isinstance(resultado, dict):
            self.estado.fijar_motor(resultado, time.monotonic())
        elif operacion == "ultimo" and isinstance(resultado, list) and resultado:
            self.estado.cargar_ultimo(resultado[0].get("text", ""))
        self._dibujar()

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
