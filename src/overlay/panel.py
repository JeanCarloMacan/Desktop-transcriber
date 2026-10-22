"""Panel flotante: sin marco, siempre encima, translúcido y sin robar el foco.

Funciona como una nota adhesiva de tamaño fijo:
- Se arrastra desde cualquier punto; los bordes y las esquinas cambian el tamaño (el alto se ajusta a líneas enteras).
- La rueda desplaza el texto; si estás leyendo más arriba, el texto nuevo no te arrastra al final.
- Ctrl + rueda cambia el tamaño de la letra.
- Recuerda posición, ancho, líneas visibles (compacto y amplio), letra y modo en `datos/overlay.json`.
- La posición se guarda anclada por abajo (x, borde inferior).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from overlay import escritorio
from overlay.estado import EstadoPanel

log = logging.getLogger(__name__)

ICONOS = {"copiar": "", "pegar": "", "editor": "", "tareas": "", "liberar": "", "ampliar": "",
          "compactar": "", "ocultar": ""}
FUENTE_ICONOS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
LINEAS_POR_DEFECTO = {False: 4, True: 14}  # compacto / amplio
MARGEN = (18, 12, 14, 14)
BORDE = 7  # píxeles de cada borde que sirven para redimensionar
ANCHO_MIN, LETRA_MIN, LETRA_MAX = 320, 9.0, 24.0
ESTILO = """
QToolButton { color: #9CA3AF; border: none; padding: 4px; }
QToolButton:hover { color: #F9FAFB; background: rgba(255, 255, 255, 0.10); border-radius: 6px; }
QToolButton:disabled { color: #4B5563; }
QTextBrowser { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.25); border-radius: 3px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""
AYUDAS = {"copiar": "Copiar el último dictado", "pegar": "Pegarlo donde está el cursor (Ctrl+Alt+V)",
          "editor": "Abrir el editor de dictados: corregir, ver capturas y copiar para la IA (Ctrl+Alt+E)",
          "tareas": "Abrir las tareas: propuestas del organizador y listas por categoría (Ctrl+Alt+P)",
          "liberar": "Terminar y liberar la GPU (Ctrl+Alt+L)", "ampliar": "Cambiar a la vista amplia",
          "ocultar": "Ocultar (Ctrl+Alt+O)"}
CURSORES = {frozenset({"izq"}): Qt.CursorShape.SizeHorCursor, frozenset({"der"}): Qt.CursorShape.SizeHorCursor,
            frozenset({"arr"}): Qt.CursorShape.SizeVerCursor, frozenset({"aba"}): Qt.CursorShape.SizeVerCursor,
            frozenset({"arr", "izq"}): Qt.CursorShape.SizeFDiagCursor,
            frozenset({"aba", "der"}): Qt.CursorShape.SizeFDiagCursor,
            frozenset({"arr", "der"}): Qt.CursorShape.SizeBDiagCursor,
            frozenset({"aba", "izq"}): Qt.CursorShape.SizeBDiagCursor}


class Punto(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._color = QColor("#6B7280")

    def fijar(self, color: str) -> None:
        if QColor(color) != self._color:
            self._color = QColor(color)
            self.update()

    def paintEvent(self, evento: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        lado = min(self.width(), self.height()) - 2
        p.drawEllipse((self.width() - lado) // 2, (self.height() - lado) // 2, lado, lado)


class Panel(QWidget):
    accion = Signal(str)
    raton = Signal(bool)

    def __init__(self, ancho: int, letra: float, opacidad: float, ruta_estado: Path, con_tareas: bool = False) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)  # el panel nunca está activo: sin esto no salen
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setWindowTitle("Transcriptor RT")
        self.setStyleSheet(ESTILO)
        self._opacidad = opacidad
        self._ruta = ruta_estado
        self._ancho, self._letra, self._amplio = ancho, letra, False
        self._lineas = dict(LINEAS_POR_DEFECTO)
        self._html: str | None = None
        self._arrastre: QPoint | None = None
        # (zona, punto global inicial, ancho inicial, x inicial, borde inferior inicial, alto del texto inicial)
        self._redimension: tuple[frozenset[str], QPoint, int, int, int, int] | None = None
        self._ancla: tuple[int, int] | None = None  # (x, borde inferior)
        self._sin_activar = False

        caja = QVBoxLayout(self)
        caja.setContentsMargins(*MARGEN)
        caja.setSpacing(6)
        cabecera = QHBoxLayout()
        cabecera.setSpacing(4)
        self._punto = Punto()
        self._titulo = QLabel()
        self._titulo.setStyleSheet("color: #D1D5DB;")
        cabecera.addWidget(self._punto)
        cabecera.addSpacing(4)
        cabecera.addWidget(self._titulo, 1)
        self._botones: dict[str, QToolButton] = {}
        for nombre, ayuda in AYUDAS.items():
            if nombre == "tareas" and not con_tareas:
                continue
            b = QToolButton()
            b.setText(ICONOS[nombre])
            b.setToolTip(ayuda)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=nombre: self._pulsar(n))
            cabecera.addWidget(b)
            self._botones[nombre] = b
        caja.addLayout(cabecera)

        self._cuerpo = QTextBrowser()
        self._cuerpo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cuerpo.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._cuerpo.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cuerpo.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._cuerpo.viewport().setAutoFillBackground(False)
        self._cuerpo.viewport().setMouseTracking(True)
        self._cuerpo.viewport().installEventFilter(self)  # el texto también sirve para arrastrar y redimensionar
        self._cuerpo.document().setDocumentMargin(2)
        caja.addWidget(self._cuerpo)

        self._pie = QLabel()
        self._pie.setWordWrap(True)
        caja.addWidget(self._pie)

        self._animacion = QPropertyAnimation(self, b"windowOpacity")
        self._animacion.setDuration(180)
        self._animacion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animacion.finished.connect(self._al_terminar_animacion)
        self._cargar()
        self._aplicar_letra()
        self._aplicar_modo()

    # ------------------------------------------------------------ contenido

    def actualizar(self, estado: EstadoPanel, ahora: float) -> None:
        self.refrescar_titulo(estado, ahora)
        # Primero el pie: si aparece o desaparece, cambia el alto del panel y hay que volver a medir.
        cambia_pie = self._pie.isVisibleTo(self) != bool(estado.aviso) or self._pie.text() != estado.aviso
        self._pie.setText(estado.aviso)
        self._pie.setStyleSheet(f"color: {'#FCA5A5' if estado.aviso_error else '#9CA3AF'};")
        self._pie.setVisible(bool(estado.aviso))
        html = estado.html()
        if html != self._html:
            self._html = html
            self._poner_html(html)
        elif cambia_pie:
            self._ajustar_alto()
        self._botones["copiar"].setEnabled(bool(estado.ultimo_texto))
        self._botones["pegar"].setEnabled(bool(estado.ultimo_texto))
        self._recolocar()

    def _poner_html(self, html: str) -> None:
        """Cambia el texto sin perder tu sitio: si estabas al final lo sigue; si leías más arriba, no te mueve."""
        barra = self._cuerpo.verticalScrollBar()
        al_final = barra.value() >= barra.maximum() - 4
        anterior = barra.value()
        self._cuerpo.setHtml(html)
        self._ajustar_alto()
        barra.setValue(barra.maximum() if al_final else min(anterior, barra.maximum()))

    def refrescar_titulo(self, estado: EstadoPanel, ahora: float) -> None:
        self._titulo.setText(estado.titulo(datetime.now(UTC), ahora))
        self._punto.fijar(estado.color_punto)

    def _aplicar_letra(self) -> None:
        self._cuerpo.setFont(QFont("Segoe UI", self._letra))
        self._titulo.setFont(QFont("Segoe UI", max(8.0, self._letra * 0.75)))
        self._pie.setFont(QFont("Segoe UI", max(8.0, self._letra * 0.7)))
        iconos = QFont()
        iconos.setFamilies(FUENTE_ICONOS)
        iconos.setPointSizeF(self._letra * 0.95)  # en puntos: con píxeles Qt avisa «Point size <= 0» al heredarla
        for b in self._botones.values():
            b.setFont(iconos)
        lado = round(self._letra * 0.8)
        self._punto.setFixedSize(lado, lado)

    def _alto_linea(self) -> float:
        """Alto real de una línea maquetada (no el de QFontMetrics): así el alto cae justo en líneas enteras."""
        doc = self._cuerpo.document()
        doc.size()  # fuerza a maquetar con la letra actual antes de medir
        bloque = doc.firstBlock()
        maqueta = bloque.layout() if bloque.isValid() else None
        if maqueta is not None and maqueta.lineCount() > 0:
            return maqueta.lineAt(0).height()
        return float(QFontMetrics(self._cuerpo.font()).lineSpacing())

    def _lineas_max(self) -> int:
        """Las que caben en la pantalla dejando sitio para la cabecera, el pie y los márgenes."""
        disponible = QGuiApplication.primaryScreen().availableGeometry().height() - 160
        return max(1, int(disponible // self._alto_linea()))

    @property
    def lineas(self) -> int:
        return self._lineas[self._amplio]

    def _ajustar_alto(self) -> None:
        """Alto fijo de `lineas` líneas, como una nota adhesiva: el texto que no cabe se desplaza."""
        barra = self._cuerpo.verticalScrollBar()
        al_final = barra.value() >= barra.maximum() - 4
        self.setFixedWidth(self._ancho)
        doc = self._cuerpo.document()
        izq, _, der, _ = MARGEN
        doc.setTextWidth(self._ancho - izq - der - 8)  # 8 = barra de desplazamiento fina
        linea = self._alto_linea()
        lineas = min(self.lineas, self._lineas_max())
        self._cuerpo.setFixedHeight(round(lineas * linea + 2 * doc.documentMargin()) + 2 * self._cuerpo.frameWidth())
        caja = self.layout()
        caja.invalidate()  # sin esto, adjustSize usa tamaños en caché de antes del cambio
        caja.activate()
        self.adjustSize()
        if al_final:
            barra.setValue(barra.maximum())

    def _aplicar_modo(self) -> None:
        self._botones["ampliar"].setText(ICONOS["compactar" if self._amplio else "ampliar"])
        self._botones["ampliar"].setToolTip("Cambiar a la vista compacta" if self._amplio
                                            else "Cambiar a la vista amplia")
        self._ajustar_alto()

    def alternar_tamano(self) -> None:
        self._amplio = not self._amplio
        self._aplicar_modo()
        self._recolocar()
        self._guardar()

    def cambiar_letra(self, pasos: int) -> None:
        letra = min(LETRA_MAX, max(LETRA_MIN, self._letra + 0.5 * pasos))
        if letra != self._letra:
            self._letra = letra
            self._aplicar_letra()
            if self._html is not None:
                self._poner_html(self._html)  # el HTML importado conserva la letra anterior: se reimporta
            else:
                self._ajustar_alto()
            self._recolocar()
            self._guardar()

    def desplazar_al_final(self) -> None:
        barra = self._cuerpo.verticalScrollBar()
        barra.setValue(barra.maximum())

    # ------------------------------------------------------------ redimensionar

    def redimensionar(self, zona: frozenset[str], dx: int, dy: int) -> None:
        """Aplica un arrastre de (dx, dy) píxeles desde el inicio del redimensionado sobre los bordes de `zona`."""
        if self._redimension is None:
            return
        _, _, ancho0, x0, abajo0, alto0 = self._redimension
        x, abajo = x0, abajo0
        if zona & {"izq", "der"}:
            maximo = QGuiApplication.primaryScreen().availableGeometry().width() - 32
            self._ancho = min(maximo, max(ANCHO_MIN, ancho0 + dx if "der" in zona else ancho0 - dx))
            if "izq" in zona:
                x = x0 + (ancho0 - self._ancho)
        if zona & {"arr", "aba"}:
            alto = alto0 - dy if "arr" in zona else alto0 + dy
            margen = 2 * self._cuerpo.document().documentMargin()
            self._lineas[self._amplio] = min(self._lineas_max(), max(1, round((alto - margen) / self._alto_linea())))
            if "aba" in zona:
                abajo = abajo0 + dy
        self._ajustar_alto()
        if "aba" in zona:  # se ajustó a líneas enteras: el borde inferior sigue al cuerpo real
            abajo = abajo0 + (self._cuerpo.height() - alto0)
        self._ancla = (x, abajo)
        self._recolocar()

    def _empezar_redimension(self, zona: frozenset[str], inicio: QPoint) -> None:
        self._redimension = (zona, inicio, self._ancho, self.x(), self.y() + self.height() - 1, self._cuerpo.height())

    # ------------------------------------------------------------ mostrar y ocultar

    def mostrar(self) -> None:
        if self.isVisible() and self._animacion.endValue() == 1.0:
            return
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._recolocar()
        self._animar(1.0)

    def ocultar(self) -> None:
        if self.isVisible():
            self._animar(0.0)

    def _animar(self, destino: float) -> None:
        self._animacion.stop()
        self._animacion.setStartValue(self.windowOpacity())
        self._animacion.setEndValue(destino)
        self._animacion.start()

    def _al_terminar_animacion(self) -> None:
        if self._animacion.endValue() == 0.0:
            self.hide()

    def showEvent(self, evento: object) -> None:
        if not self._sin_activar:
            escritorio.no_activar(int(self.winId()))
            self._sin_activar = True

    # ------------------------------------------------------------ posición y tamaño guardados

    def _ancla_por_defecto(self) -> tuple[int, int]:
        area = QGuiApplication.primaryScreen().availableGeometry()
        return area.right() - self._ancho - 16, area.bottom() - 16

    def _recolocar(self) -> None:
        x, abajo = self._ancla or self._ancla_por_defecto()
        area = QGuiApplication.primaryScreen().availableGeometry()
        arriba = max(area.top(), abajo - self.height() + 1)  # nunca por encima del borde superior de la pantalla
        self.move(x, arriba)

    def _cargar(self) -> None:
        try:
            datos = json.loads(self._ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            self._ancho = max(ANCHO_MIN, int(datos.get("ancho", self._ancho)))
            self._letra = min(LETRA_MAX, max(LETRA_MIN, float(datos.get("letra", self._letra))))
            self._amplio = bool(datos.get("amplio", False))
            compacto, amplio = datos.get("lineas", [LINEAS_POR_DEFECTO[False], LINEAS_POR_DEFECTO[True]])
            self._lineas = {False: max(1, int(compacto)), True: max(1, int(amplio))}
            x, abajo = int(datos["x"]), int(datos["abajo"])
        except (ValueError, KeyError, TypeError):
            return
        visible = any(p.availableGeometry().contains(QPoint(x + 20, abajo - 20)) for p in QGuiApplication.screens())
        self._ancla = (x, abajo) if visible else None
        if not visible:
            log.info("La posición guardada del panel quedó fuera de pantalla: se usa la esquina por defecto")

    def _guardar(self) -> None:
        x, abajo = self._ancla or self._ancla_por_defecto()
        datos = {"x": x, "abajo": abajo, "amplio": self._amplio, "ancho": self._ancho, "letra": self._letra,
                 "lineas": [self._lineas[False], self._lineas[True]]}
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            self._ruta.write_text(json.dumps(datos), encoding="utf-8")
        except OSError as e:
            log.warning("No se pudo guardar la posición del panel: %s", e)

    # ------------------------------------------------------------ ratón

    def _pulsar(self, nombre: str) -> None:
        if nombre == "ampliar":
            self.alternar_tamano()
        self.accion.emit(nombre)

    def _zona(self, local: QPoint) -> frozenset[str]:
        zona = set()
        if local.x() < BORDE:
            zona.add("izq")
        elif local.x() > self.width() - BORDE:
            zona.add("der")
        if local.y() < BORDE:
            zona.add("arr")
        elif local.y() > self.height() - BORDE:
            zona.add("aba")
        return frozenset(zona)

    def _cursor(self, zona: frozenset[str]) -> None:
        forma = CURSORES.get(zona, Qt.CursorShape.SizeAllCursor)
        self.setCursor(forma)
        self._cuerpo.viewport().setCursor(forma)

    def mousePressEvent(self, evento: object) -> None:
        if evento.button() != Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
            return
        global_ = evento.globalPosition().toPoint()  # type: ignore[attr-defined]
        zona = self._zona(self.mapFromGlobal(global_))
        if zona:
            self._empezar_redimension(zona, global_)
        else:
            self._arrastre = global_ - self.pos()

    def mouseMoveEvent(self, evento: object) -> None:
        global_ = evento.globalPosition().toPoint()  # type: ignore[attr-defined]
        if self._redimension is not None:
            zona, inicio = self._redimension[0], self._redimension[1]
            self.redimensionar(zona, global_.x() - inicio.x(), global_.y() - inicio.y())
        elif self._arrastre is not None:
            nueva = global_ - self._arrastre
            self.move(nueva)
            self._ancla = (nueva.x(), nueva.y() + self.height() - 1)
        else:
            self._cursor(self._zona(self.mapFromGlobal(global_)))

    def mouseReleaseEvent(self, evento: object) -> None:
        if self._arrastre is not None or self._redimension is not None:
            self._arrastre = self._redimension = None
            self._guardar()

    def wheelEvent(self, evento: object) -> None:
        if evento.modifiers() & Qt.KeyboardModifier.ControlModifier:  # type: ignore[attr-defined]
            self.cambiar_letra(1 if evento.angleDelta().y() > 0 else -1)  # type: ignore[attr-defined]
            evento.accept()  # type: ignore[attr-defined]
        else:
            evento.ignore()  # type: ignore[attr-defined]

    def eventFilter(self, objeto: object, evento: QEvent) -> bool:
        tipo = evento.type()
        if tipo == QEvent.Type.Wheel:
            if evento.modifiers() & Qt.KeyboardModifier.ControlModifier:  # type: ignore[attr-defined]
                self.wheelEvent(evento)
                return True
            return False  # sin Ctrl, la rueda desplaza el texto
        manejadores = {QEvent.Type.MouseButtonPress: self.mousePressEvent,
                       QEvent.Type.MouseMove: self.mouseMoveEvent,
                       QEvent.Type.MouseButtonRelease: self.mouseReleaseEvent}
        if (manejar := manejadores.get(tipo)) is not None:
            manejar(evento)
            return True
        return False

    def enterEvent(self, evento: object) -> None:
        self.raton.emit(True)

    def leaveEvent(self, evento: object) -> None:
        self.raton.emit(False)

    def paintEvent(self, evento: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        forma = QPainterPath()
        forma.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        p.fillPath(forma, QColor(17, 24, 39, int(255 * self._opacidad)))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(forma)
