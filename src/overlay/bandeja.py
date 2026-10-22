"""Icono en la bandeja del sistema: color según el estado del motor y menú con las acciones."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from overlay.estado import EstadoPanel

MODOS = [("auto", "Automático (5080 si hay espacio)"), ("gpu", "Solo GPU 5080"), ("cpu", "Solo CPU"),
         ("pausado", "En pausa")]


def icono(color: str) -> QIcon:
    """Cuadrado redondeado del color del estado con un micrófono blanco."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    fuente = QFont()
    fuente.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
    fuente.setPixelSize(38)
    p.setFont(fuente)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "")
    p.end()
    return QIcon(pixmap)


class Bandeja(QSystemTrayIcon):
    accion = Signal(str)
    modo = Signal(str)

    def __init__(self, tecla_mostrar: str, tecla_pegar: str, tecla_liberar: str, tecla_capturar: str,
                 dueño_servicio: bool, con_tareas: bool = False, tecla_tareas: str = "", tecla_editor: str = "") -> None:
        super().__init__()
        self._iconos: dict[str, QIcon] = {}
        menu = QMenu()
        self._acciones: dict[str, QAction] = {}
        entradas = [("mostrar", f"Mostrar u ocultar el panel\t{tecla_mostrar}"),
                    ("editor", f"Abrir el editor de dictados\t{tecla_editor}" if tecla_editor
                     else "Abrir el editor de dictados"),
                    ("tareas", f"Abrir las tareas\t{tecla_tareas}" if tecla_tareas else "Abrir las tareas"),
                    ("capturar", f"Capturar pantalla para el dictado\t{tecla_capturar}"),
                    ("copiar", "Copiar el último dictado"),
                    ("pegar", f"Pegar el último dictado\t{tecla_pegar}")]
        for nombre, texto in entradas:
            if nombre == "tareas" and not con_tareas:
                continue
            self._acciones[nombre] = menu.addAction(texto, lambda n=nombre: self.accion.emit(n))
        menu.addSeparator()
        submenu = menu.addMenu("Modo del motor")
        grupo = QActionGroup(submenu)
        self._modos: dict[str, QAction] = {}
        for clave, texto in MODOS:
            a = submenu.addAction(texto, lambda c=clave: self.modo.emit(c))
            a.setCheckable(True)
            grupo.addAction(a)
            self._modos[clave] = a
        self._acciones["liberar"] = menu.addAction(f"Liberar la GPU ahora\t{tecla_liberar}",
                                                   lambda: self.accion.emit("liberar"))
        menu.addSeparator()
        menu.addAction("Abrir la carpeta de datos", lambda: self.accion.emit("carpeta"))
        menu.addAction("Ver el registro", lambda: self.accion.emit("log"))
        menu.addSeparator()
        menu.addAction("Salir (cierra también el servicio)" if dueño_servicio else "Salir",
                       lambda: self.accion.emit("salir"))
        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._al_activar)

    def _al_activar(self, motivo: QSystemTrayIcon.ActivationReason) -> None:
        if motivo == QSystemTrayIcon.ActivationReason.Trigger:
            self.accion.emit("mostrar")

    def actualizar(self, estado: EstadoPanel, titulo: str) -> None:
        color = estado.color_punto
        if color not in self._iconos:
            self._iconos[color] = icono(color)
        self.setIcon(self._iconos[color])
        self.setToolTip(f"Transcriptor RT · {titulo}")
        for clave, accion in self._modos.items():
            accion.setChecked(clave == estado.modo)
        hay_texto = bool(estado.ultimo_texto)
        self._acciones["copiar"].setEnabled(hay_texto)
        self._acciones["pegar"].setEnabled(hay_texto)
        self._acciones["liberar"].setEnabled(estado.conectado and estado.motor != "apagado")
