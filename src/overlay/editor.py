"""Editor de dictados: lista de sesiones + documento con las capturas en su sitio, y «Copiar para IA».

Es una ventana normal (sí toma el foco, a diferencia del panel). Habla con voice-core por el Cliente (REST en
hilos; las respuestas llegan por la señal `respuesta` con operaciones «editor:*»). El documento se edita como texto
enriquecido y se guarda como Markdown (`QTextEdit.toMarkdown`), con los párrafos re-unidos.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QCloseEvent, QDrag, QFont, QGuiApplication, QImageReader, QKeySequence, QPixmap, QShortcut,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextImageFormat)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from overlay.cliente import Cliente
from voice_core import documento

log = logging.getLogger(__name__)

OBJETO = "￼"  # carácter que representa una imagen dentro del texto del documento
ESTILO = """
QWidget { font-family: 'Segoe UI'; }
QListWidget { border: none; background: #F3F4F6; padding: 6px; }
QListWidget::item { padding: 8px 6px; border-radius: 6px; color: #1F2933; }
QListWidget::item:selected { background: #DBEAFE; color: #1E3A8A; }
QTextEdit { border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px; background: #FFFFFF; }
QPushButton { padding: 6px 12px; border: 1px solid #D1D5DB; border-radius: 6px; background: #FFFFFF; }
QPushButton:hover { background: #F3F4F6; }
QPushButton:disabled { color: #9CA3AF; }
QPushButton#principal { background: #2563EB; color: white; border-color: #2563EB; }
QPushButton#principal:hover { background: #1D4ED8; }
"""


def _rotulo(s: dict) -> str:
    inicio = datetime.fromisoformat(s["started_at"]).astimezone()
    hoy = datetime.now().astimezone().date()
    dia = "hoy" if inicio.date() == hoy else f"{inicio:%d/%m}"
    marcas = [f"{dia} {inicio:%H:%M}", s["mode"]]
    if s.get("attachments"):
        marcas.append(f"📷 {s['attachments']}")
    if s.get("edited_at"):
        marcas.append("✎")
    texto = (s.get("text") or "").strip() or "(sin voz)"
    return " · ".join(marcas) + "\n" + (texto[:70] + "…" if len(texto) > 70 else texto)


class ArrastrarImagenes(QFrame):
    """Asa para arrastrar todas las capturas a la vez a un chat: se sueltan como archivos PNG."""

    def __init__(self) -> None:
        super().__init__()
        self._rutas: list[Path] = []
        self._inicio: QPoint | None = None
        self._etiqueta = QLabel()
        caja = QHBoxLayout(self)
        caja.setContentsMargins(10, 4, 10, 4)
        caja.addWidget(self._etiqueta)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet("ArrastrarImagenes { border: 1px dashed #7C3AED; border-radius: 6px; background: #F5F3FF; }"
                           "QLabel { color: #5B21B6; }")
        self.fijar([])

    @property
    def rutas(self) -> list[Path]:
        return list(self._rutas)

    def fijar(self, rutas: list[Path]) -> None:
        self._rutas = [r for r in rutas if r.is_file()]
        n = len(self._rutas)
        self._etiqueta.setText("⠿ Sin capturas" if n == 0 else f"⠿ Arrastra {n} {'imagen' if n == 1 else 'imágenes'} "
                               "al chat")
        self.setEnabled(n > 0)
        self.setToolTip("Arrastra desde aquí y suelta en el chat (Claude, ChatGPT…): se adjuntan todas a la vez")

    def mousePressEvent(self, evento: Any) -> None:
        if evento.button() == Qt.MouseButton.LeftButton and self._rutas:
            self._inicio = evento.position().toPoint()

    def mouseMoveEvent(self, evento: Any) -> None:
        if self._inicio is None:
            return
        if (evento.position().toPoint() - self._inicio).manhattanLength() < QApplication.startDragDistance():
            return
        self._inicio = None
        datos = QMimeData()
        datos.setUrls([QUrl.fromLocalFile(str(r)) for r in self._rutas])
        arrastre = QDrag(self)
        arrastre.setMimeData(datos)
        miniatura = QPixmap(str(self._rutas[0]))
        if not miniatura.isNull():
            arrastre.setPixmap(miniatura.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        arrastre.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, evento: Any) -> None:
        self._inicio = None


class Editor(QWidget):
    capturar = Signal(str)  # pide al controlador un recorte para insertarlo en el cursor de esta sesión
    extraer_tareas = Signal(str)  # pide al controlador que tasks-service extraiga las tareas de esta sesión

    def __init__(self, cliente: Cliente, con_tareas: bool = False) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Transcriptor RT · Dictados")
        self.setStyleSheet(ESTILO)
        self.resize(1180, 760)
        self._cliente = cliente
        self._sesion: str | None = None
        self._carpeta: Path | None = None
        self._pendiente: str | None = None
        self._modificado = False
        self._cargando = False
        self._llenando = False
        self._insertando = False  # se pidió una captura para insertar en el cursor y aún no llegó
        self._extraer_al_guardar: str | None = None  # «Extraer tareas» con cambios: primero se guardan

        self._lista = QListWidget()
        self._lista.setMinimumWidth(260)
        self._lista.setWordWrap(True)
        self._lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lista.currentItemChanged.connect(self._al_elegir)

        self._titulo = QLabel()
        self._titulo.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._texto = QTextEdit()
        self._texto.setAcceptRichText(False)  # lo pegado entra como texto; las imágenes, por «Insertar captura»
        self._texto.setFont(QFont("Segoe UI", 11))
        self._texto.textChanged.connect(self._al_cambiar)

        self._botones: dict[str, QPushButton] = {}

        def boton(clave: str, texto: str, ayuda: str, accion: Any, principal: bool = False) -> QPushButton:
            b = QPushButton(texto)
            b.setToolTip(ayuda)
            b.clicked.connect(accion)
            if principal:
                b.setObjectName("principal")
            self._botones[clave] = b
            return b

        edicion = QHBoxLayout()
        edicion.addWidget(boton("guardar", "Guardar", "Guardar los cambios (Ctrl+S)", self.guardar, principal=True))
        edicion.addWidget(boton("original", "Volver al original", "Descartar la edición y volver al dictado",
                                self.volver_al_original))
        edicion.addWidget(boton("captura", "Insertar captura", "Recorte de pantalla en la posición del cursor",
                                self.insertar_captura))
        edicion.addStretch(1)
        edicion.addWidget(boton("carpeta", "Abrir carpeta", "documento.md, capturas y audio de la sesión",
                                self.abrir_carpeta))

        ia = QHBoxLayout()
        ia.addWidget(boton("chat", "Copiar para chat", "Texto con [Imagen N] en su sitio; luego arrastra las imágenes",
                           self.copiar_para_chat, principal=True))
        self._arrastre = ArrastrarImagenes()
        ia.addWidget(self._arrastre)
        ia.addWidget(boton("agente", "Copiar para agente", "Markdown con las rutas absolutas de las imágenes "
                           "(Claude Code y otros agentes)", self.copiar_para_agente))
        ia.addStretch(1)
        if con_tareas:
            ia.addWidget(boton("tareas", "Extraer tareas", "El organizador busca tareas en este dictado (LM Studio); "
                               "llegan a la ventana Tareas como propuestas", self.extraer))

        self._estado = QLabel()
        self._estado.setStyleSheet("color: #6B7280;")

        derecha = QWidget()
        caja = QVBoxLayout(derecha)
        caja.setContentsMargins(12, 10, 12, 10)
        caja.addWidget(self._titulo)
        caja.addLayout(edicion)
        caja.addWidget(self._texto, 1)
        caja.addLayout(ia)
        caja.addWidget(self._estado)

        division = QSplitter()
        division.addWidget(self._lista)
        division.addWidget(derecha)
        division.setStretchFactor(1, 1)
        division.setSizes([300, 880])
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.addWidget(division)

        QShortcut(QKeySequence.StandardKey.Save, self, self.guardar)
        self._escalado = QTimer(self, singleShot=True, interval=150)
        self._escalado.timeout.connect(self._escalar_imagenes)
        cliente.respuesta.connect(self._respuesta)
        self._habilitar()

    # ------------------------------------------------------------ abrir y lista

    def abrir(self, sesion_id: str | None = None) -> None:
        self._pendiente = sesion_id or self._sesion
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self.refrescar()

    def refrescar(self) -> None:
        self._cliente.pedir("editor:lista", "GET", "/sessions?limit=200&status=final")

    def al_evento(self, ev: dict) -> None:
        """Eventos de voice-core reenviados por el controlador."""
        if not self.isVisible() or ev.get("type") not in ("session.finalized", "attachment.added", "session.edited"):
            return
        self._pendiente = self._sesion
        self.refrescar()
        propio = ev.get("session_id") == self._sesion
        if propio and ev["type"] == "attachment.added" and not self._modificado and not self._insertando:
            self._cliente.pedir("editor:doc", "GET", f"/sessions/{self._sesion}/document")

    def _llenar_lista(self, sesiones: list[dict]) -> None:
        objetivo = self._pendiente or self._sesion
        self._pendiente = None
        self._llenando = True
        self._lista.clear()
        fila = None
        for i, s in enumerate(sesiones):
            item = QListWidgetItem(_rotulo(s))
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            self._lista.addItem(item)
            if s["id"] == objetivo:
                fila = i
        self._llenando = False
        if fila is None and self._lista.count() and self._sesion is None:
            fila = 0
        if fila is not None:
            self._lista.setCurrentRow(fila)  # tras clear() la fila actual es -1: esto dispara _al_elegir
        if not sesiones:
            self._avisar("Todavía no hay dictados terminados")

    def _al_elegir(self, actual: QListWidgetItem | None, anterior: QListWidgetItem | None) -> None:
        if self._llenando or actual is None:
            return
        sid = actual.data(Qt.ItemDataRole.UserRole)
        if sid == self._sesion and self._carpeta is not None:
            return
        if self._modificado and self._sesion:
            self.guardar()  # autoguardado al cambiar de sesión
        self._sesion = sid
        self._cliente.pedir("editor:doc", "GET", f"/sessions/{sid}/document")

    # ------------------------------------------------------------ documento

    def _respuesta(self, operacion: str, resultado: object) -> None:
        if not operacion.startswith("editor:"):
            return
        if isinstance(resultado, Exception):
            self._avisar(f"No se pudo: {resultado}", error=True)
            return
        if operacion == "editor:lista" and isinstance(resultado, list):
            self._llenar_lista(resultado)
        elif operacion == "editor:doc" and isinstance(resultado, dict):
            self.mostrar_documento(resultado)
        elif operacion == "editor:guardar" and isinstance(resultado, dict):
            self._avisar(f"Guardado a las {datetime.now():%H:%M:%S} · también en documento.md")
            self._botones["original"].setEnabled(True)
            if self._extraer_al_guardar == resultado.get("session_id"):
                self._extraer_al_guardar = None
                self.extraer_tareas.emit(resultado["session_id"])
        elif operacion == "editor:original" and isinstance(resultado, dict):
            self.mostrar_documento(resultado)
            self._avisar("De vuelta al texto original del dictado")

    def mostrar_documento(self, d: dict) -> None:
        if d["session_id"] != self._sesion:
            return  # respuesta de una sesión que ya no está seleccionada
        self._carpeta = Path(d["folder"])
        self._cargando = True
        self._texto.document().setBaseUrl(QUrl.fromLocalFile(str(self._carpeta) + "/"))
        self._texto.setMarkdown(d["markdown"])
        self._escalar_imagenes()
        self._cargando = False
        self._modificado = False
        primera = d["markdown"].lstrip().split("\n", 1)[0]
        self._titulo.setText(primera[2:] if primera.startswith("# ") else "Dictado")
        self._arrastre.fijar([self._carpeta / r for r in documento.imagenes(d["markdown"])])
        self._botones["original"].setEnabled(bool(d["edited"]))
        self._avisar("Editado a mano" if d["edited"] else "Texto original del dictado: edítalo aquí mismo")
        self._habilitar()

    def markdown(self) -> str:
        return documento.unir_lineas(self._texto.toMarkdown())

    def _al_cambiar(self) -> None:
        if self._cargando or self._sesion is None:
            return
        self._modificado = True
        self.setWindowTitle("Transcriptor RT · Dictados — sin guardar")

    def guardar(self) -> None:
        if self._sesion is None:
            return
        self._cliente.pedir("editor:guardar", "PUT", f"/sessions/{self._sesion}/document", {"markdown": self.markdown()})
        self._modificado = False
        self.setWindowTitle("Transcriptor RT · Dictados")
        self._arrastre.fijar(self._rutas_actuales())

    def volver_al_original(self) -> None:
        if self._sesion is None:
            return
        respuesta = QMessageBox.question(self, "Volver al original",
                                         "Se descartan tus correcciones y vuelve el texto tal como se dictó. ¿Seguro?")
        if respuesta == QMessageBox.StandardButton.Yes:
            self._modificado = False
            self._cliente.pedir("editor:original", "DELETE", f"/sessions/{self._sesion}/document")

    def _rutas_actuales(self) -> list[Path]:
        if self._carpeta is None:
            return []
        return [Path(r) if Path(r).is_absolute() else self._carpeta / r for r in documento.imagenes(self.markdown())]

    # ------------------------------------------------------------ para la IA

    def copiar_para_chat(self) -> None:
        if self._sesion is None:
            return
        QGuiApplication.clipboard().setText(documento.para_chat(self.markdown()))
        rutas = self._rutas_actuales()
        self._arrastre.fijar(rutas)
        extra = f" y después arrastra las {len(rutas)} imágenes" if len(rutas) > 1 else (
            " y después arrastra la imagen" if rutas else "")
        self._avisar(f"Copiado para chat: pégalo con Ctrl+V{extra}")

    def copiar_para_agente(self) -> None:
        if self._sesion is None or self._carpeta is None:
            return
        if self._modificado:
            self.guardar()
        QGuiApplication.clipboard().setText(documento.con_rutas_absolutas(self.markdown(), self._carpeta))
        self._avisar(f"Copiado para agente: Markdown con rutas absolutas · archivo: {self._carpeta / 'documento.md'}")

    def abrir_carpeta(self) -> None:
        if self._carpeta is not None and self._carpeta.is_dir():
            os.startfile(self._carpeta)  # noqa: S606

    def extraer(self) -> None:
        """Las tareas salen del texto guardado: si hay cambios, se guardan antes y se extrae al confirmarse."""
        if self._sesion is None:
            return
        if self._modificado:
            self._extraer_al_guardar = self._sesion
            self.guardar()
        else:
            self.extraer_tareas.emit(self._sesion)
        self._avisar("Buscando tareas en este dictado…")

    # ------------------------------------------------------------ capturas

    def insertar_captura(self) -> None:
        if self._sesion is not None:
            self._insertando = True
            self.capturar.emit(self._sesion)

    def captura_cancelada(self) -> None:
        self._insertando = False

    def insertar_imagen(self, adjunto: dict) -> None:
        """Pone la captura recién subida tras el párrafo donde está el cursor (sin partir frases), como bloque propio
        con formato de párrafo normal (si el cursor estaba en el título, no hereda el encabezado)."""
        self._insertando = False
        if adjunto.get("session_id") != self._sesion:
            return
        cursor = self._texto.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        formato = QTextImageFormat()
        formato.setName(adjunto["file"])
        cursor.insertImage(formato)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        self._texto.setTextCursor(cursor)
        self._escalar_imagenes()
        self._arrastre.fijar(self._rutas_actuales())
        self._avisar("Captura insertada: guarda para conservar su posición")

    def _escalar_imagenes(self) -> None:
        """Imágenes al ancho de la ventana como máximo (el Markdown no guarda tamaños: solo es vista)."""
        if self._carpeta is None:
            return
        ancho = max(200, self._texto.viewport().width() - 48)
        doc = self._texto.document()
        cargando, self._cargando = self._cargando, True
        texto = doc.toPlainText()
        for pos, caracter in enumerate(texto):
            if caracter != OBJETO:
                continue
            cursor = QTextCursor(doc)
            cursor.setPosition(pos)
            cursor.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
            formato = cursor.charFormat()
            if not formato.isImageFormat():
                continue
            imagen = formato.toImageFormat()
            tam = QImageReader(str(self._carpeta / imagen.name())).size()
            if not tam.isValid() or tam.width() == 0:
                continue
            w = min(tam.width(), ancho)
            imagen.setWidth(w)
            imagen.setHeight(tam.height() * w / tam.width())
            cursor.setCharFormat(imagen)
        self._cargando = cargando

    # ------------------------------------------------------------ ventana

    def _habilitar(self) -> None:
        hay = self._sesion is not None and self._carpeta is not None
        for clave in ("guardar", "captura", "carpeta", "chat", "agente", "tareas"):
            if clave in self._botones:
                self._botones[clave].setEnabled(hay)
        if not hay:
            self._botones["original"].setEnabled(False)

    @property
    def sesion(self) -> str | None:
        return self._sesion

    def avisar(self, texto: str, error: bool = False) -> None:
        """Mensajes que llegan de fuera (p. ej. el resultado de «Extraer tareas»)."""
        self._avisar(texto, error)

    def _avisar(self, texto: str, error: bool = False) -> None:
        self._estado.setText(texto)
        self._estado.setStyleSheet(f"color: {'#B91C1C' if error else '#6B7280'};")

    def resizeEvent(self, evento: Any) -> None:
        super().resizeEvent(evento)
        self._escalado.start()

    def closeEvent(self, evento: QCloseEvent) -> None:
        if self._modificado:
            self.guardar()
        evento.accept()
