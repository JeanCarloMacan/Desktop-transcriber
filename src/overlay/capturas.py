"""Capturas con el recorte de Windows: lanza `ms-screenclip:` y espera la imagen que deja en el portapapeles.

Si se cancela el recorte (Esc), el portapapeles no cambia y la espera vence sin hacer nada.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication, QImage

log = logging.getLogger(__name__)

ESPERA_MS = 60_000


def a_png(imagen: QImage) -> bytes:
    datos = QByteArray()
    buffer = QBuffer(datos)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    imagen.save(buffer, "PNG")
    buffer.close()
    return bytes(datos.data())


def lanzar_recorte() -> None:
    os.startfile("ms-screenclip:")  # noqa: S606  # el recorte de Windows (como Win+Shift+S)


class Capturador(QObject):
    """Una captura a la vez. `terminada(con_imagen)` siempre al acabar (o cancelar); después, si hubo imagen,
    `lista(png, contexto)`."""

    lista = Signal(bytes, dict)
    terminada = Signal(bool)

    def __init__(self, portapapeles: QClipboard | None = None, lanzar: Any = lanzar_recorte) -> None:
        super().__init__()
        self._portapapeles = portapapeles or QGuiApplication.clipboard()
        self._lanzar = lanzar
        self._contexto: dict | None = None
        self._espera = QTimer(self, singleShot=True, interval=ESPERA_MS)
        self._espera.timeout.connect(self._vencer)
        self._portapapeles.dataChanged.connect(self._al_cambiar)

    @property
    def esperando(self) -> bool:
        return self._contexto is not None

    def capturar(self, contexto: dict) -> bool:
        """Abre el recorte. `contexto` viaja hasta la señal `lista` (sesión, instante, dónde insertar…)."""
        if self.esperando:
            return False
        self._contexto = contexto
        self._espera.start()
        try:
            self._lanzar()
        except OSError as e:
            log.error("No se pudo abrir el recorte de Windows: %s", e)
            self._contexto = None
            self._espera.stop()
            return False
        return True

    def _al_cambiar(self) -> None:
        if not self.esperando:
            return
        datos = self._portapapeles.mimeData()
        if datos is None or not datos.hasImage():
            return  # p. ej. copiaste texto mientras tanto: se sigue esperando
        imagen = self._portapapeles.image()
        if imagen.isNull():
            return
        contexto = self._contexto
        self._acabar(con_imagen=True)
        self.lista.emit(a_png(imagen), contexto or {})

    def _vencer(self) -> None:
        log.info("Recorte cancelado o sin imagen en %d s", ESPERA_MS // 1000)
        self._acabar(con_imagen=False)

    def _acabar(self, con_imagen: bool) -> None:
        self._espera.stop()
        self._contexto = None
        self.terminada.emit(con_imagen)
