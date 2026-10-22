"""Ventana «Tareas»: propuestas del organizador para aprobar, listas por categoría y tareas hechas.

Habla con tasks-service por su propio Cliente (operaciones «tareas:*»). Guarda una copia de las tareas que se
actualiza con los eventos `task.created` / `task.updated`, así que no vuelve a pedir la lista en cada cambio.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from overlay.cliente import Cliente
from overlay.editor import ESTILO

log = logging.getLogger(__name__)

PROPUESTAS, HECHAS = "propuestas", "hechas"
DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
ROJO, GRIS = QColor("#B91C1C"), QColor("#6B7280")
ESTILO_TAREAS = ESTILO + """
QLineEdit, QComboBox, QDateEdit, QPlainTextEdit { padding: 5px; border: 1px solid #D1D5DB; border-radius: 6px; }
"""


def fecha_corta(iso: str | None, hoy: date | None = None) -> str:
    if not iso:
        return ""
    d, hoy = date.fromisoformat(iso), hoy or date.today()
    dias = (d - hoy).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    texto = f"{DIAS_CORTOS[d.weekday()]} {d.day} {MESES_CORTOS[d.month - 1]}"
    return texto if d.year == hoy.year else f"{texto} {d.year}"


def resumen_categorias(tareas: list[dict]) -> str:
    """«Trabajo (2), Personal (1)»: adónde fue cada tarea al aprobarlas."""
    recuento: dict[str, int] = {}
    for t in tareas:
        recuento[t["category"]] = recuento.get(t["category"], 0) + 1
    return ", ".join(f"{c} ({n})" for c, n in recuento.items())


def vencida(t: dict, hoy: date | None = None) -> bool:
    return bool(t.get("due")) and t["status"] == "pendiente" and date.fromisoformat(t["due"]) < (hoy or date.today())


def rotulo(t: dict, vista: str, hoy: date | None = None) -> str:
    detalles = []
    if vista in (PROPUESTAS, HECHAS):
        detalles.append(t["category"])
    if t.get("due"):
        detalles.append(("venció " if vencida(t, hoy) else "para ") + fecha_corta(t["due"], hoy))
    if vista == HECHAS and t.get("done_at"):
        detalles.append(f"hecha {datetime.fromisoformat(t['done_at']).astimezone():%d/%m}")
    if t.get("notes"):
        detalles.append(t["notes"])
    elif vista == PROPUESTAS and t.get("quote"):
        detalles.append(f"«{t['quote']}»")
    linea = " · ".join(detalles)
    return t["title"] + (f"\n{linea[:140]}{'…' if len(linea) > 140 else ''}" if linea else "")


class DialogoTarea(QDialog):
    """Editar título, categoría, fecha y notas de una tarea."""

    def __init__(self, tarea: dict, categorias: list[str], padre: QWidget | None = None) -> None:
        super().__init__(padre)
        self.setWindowTitle("Editar tarea")
        self.setStyleSheet(ESTILO_TAREAS)
        self.setMinimumWidth(520)
        self._titulo = QLineEdit(tarea["title"])
        self._categoria = QComboBox()
        self._categoria.addItems(categorias)
        if tarea["category"] not in categorias:
            self._categoria.addItem(tarea["category"])
        self._categoria.setCurrentText(tarea["category"])
        self._con_fecha = QCheckBox("Con fecha")
        self._fecha = QDateEdit()
        self._fecha.setCalendarPopup(True)
        self._fecha.setDisplayFormat("ddd d MMM yyyy")
        d = date.fromisoformat(tarea["due"]) if tarea.get("due") else date.today()
        self._fecha.setDate(QDate(d.year, d.month, d.day))
        self._con_fecha.setChecked(bool(tarea.get("due")))
        self._fecha.setEnabled(bool(tarea.get("due")))
        self._con_fecha.toggled.connect(self._fecha.setEnabled)
        self._notas = QPlainTextEdit(tarea.get("notes") or "")
        self._notas.setFixedHeight(90)
        fila_fecha = QHBoxLayout()
        fila_fecha.addWidget(self._con_fecha)
        fila_fecha.addWidget(self._fecha, 1)
        formulario = QFormLayout()
        formulario.addRow("Tarea", self._titulo)
        formulario.addRow("Categoría", self._categoria)
        formulario.addRow("Fecha", fila_fecha)
        formulario.addRow("Notas", self._notas)
        if tarea.get("quote"):
            cita = QLabel(f"Del dictado: «{tarea['quote']}»")
            cita.setWordWrap(True)
            cita.setStyleSheet("color: #6B7280;")
            formulario.addRow(cita)
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        caja = QVBoxLayout(self)
        caja.addLayout(formulario)
        caja.addWidget(botones)

    def cambios(self) -> dict[str, Any]:
        q = self._fecha.date()
        notas = self._notas.toPlainText().strip()
        return {"title": self._titulo.text().strip(), "category": self._categoria.currentText(),
                "due": date(q.year(), q.month(), q.day()).isoformat() if self._con_fecha.isChecked() else None,
                "notes": notas or None}


class VentanaTareas(QWidget):
    ver_dictado = Signal(str)  # abrir el editor en la sesión de la que salió una tarea

    def __init__(self, cliente: Cliente) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Transcriptor RT · Tareas")
        self.setStyleSheet(ESTILO_TAREAS)
        self.resize(1000, 680)
        self._cliente = cliente
        self.tareas: dict[int, dict] = {}
        self.categorias: list[dict] = []
        self._vista = PROPUESTAS
        self._llenando = False

        self._vistas = QListWidget()
        self._vistas.setMinimumWidth(220)
        self._vistas.currentItemChanged.connect(self._al_elegir_vista)

        self._cabecera = QLabel()
        self._cabecera.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._descripcion = QLabel()
        self._descripcion.setStyleSheet("color: #6B7280;")
        self._lista = QListWidget()
        self._lista.setWordWrap(True)
        self._lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lista.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._lista.setStyleSheet("QListWidget { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px; }")
        self._lista.itemChanged.connect(self._al_marcar)
        self._lista.itemDoubleClicked.connect(lambda _: self.editar())
        self._lista.itemSelectionChanged.connect(self._habilitar)

        self._botones: dict[str, QPushButton] = {}
        acciones = QHBoxLayout()
        for clave, texto, ayuda, funcion, principal in [
            ("aprobar", "Aprobar", "Pasar las seleccionadas a su lista", self.aprobar, True),
            ("todas", "Aprobar todas", "Pasar todas las propuestas a sus listas", self.aprobar_todas, False),
            ("reabrir", "Reabrir", "Volver a pendiente", self.reabrir, False),
            ("editar", "Editar…", "Título, categoría, fecha y notas (o doble clic)", self.editar, False),
            ("descartar", "Descartar", "Quitar las seleccionadas", self.descartar, False),
            ("dictado", "Ver dictado", "Abrir en el editor el dictado del que salió", self.abrir_dictado, False),
        ]:
            b = QPushButton(texto)
            b.setToolTip(ayuda)
            b.clicked.connect(funcion)
            if principal:
                b.setObjectName("principal")
            acciones.addWidget(b)
            self._botones[clave] = b
        acciones.addStretch(1)

        self._nueva = QLineEdit()
        self._nueva.returnPressed.connect(self.anadir)
        self._estado = QLabel()
        self._estado.setStyleSheet("color: #6B7280;")
        self._estado.setWordWrap(True)

        derecha = QWidget()
        caja = QVBoxLayout(derecha)
        caja.setContentsMargins(12, 10, 12, 10)
        caja.addWidget(self._cabecera)
        caja.addWidget(self._descripcion)
        caja.addLayout(acciones)
        caja.addWidget(self._lista, 1)
        caja.addWidget(self._nueva)
        caja.addWidget(self._estado)
        division = QSplitter()
        division.addWidget(self._vistas)
        division.addWidget(derecha)
        division.setStretchFactor(1, 1)
        division.setSizes([240, 760])
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.addWidget(division)
        cliente.respuesta.connect(self._respuesta)
        self._pintar()

    # ------------------------------------------------------------ datos

    def abrir(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self.refrescar()

    def refrescar(self) -> None:
        self._cliente.pedir("tareas:categorias", "GET", "/categories")
        for estado in ("propuesta", "pendiente"):
            self._cliente.pedir("tareas:lista", "GET", f"/tasks?status={estado}")
        self._cliente.pedir("tareas:lista", "GET", "/tasks?status=hecha&limit=200")
        self._cliente.pedir("tareas:llm", "GET", "/llm")

    def _respuesta(self, operacion: str, resultado: object) -> None:
        if not operacion.startswith("tareas:"):
            return
        if isinstance(resultado, Exception):
            if operacion != "tareas:llm":
                self.avisar(f"No se pudo: {resultado}", error=True)
            return
        if operacion == "tareas:categorias" and isinstance(resultado, list):
            self.categorias = resultado
        elif operacion == "tareas:lista" and isinstance(resultado, list):
            for t in resultado:
                self.tareas[t["id"]] = t
        elif operacion in ("tareas:editar", "tareas:crear") and isinstance(resultado, dict):
            self.tareas[resultado["id"]] = resultado
        elif operacion == "tareas:llm" and isinstance(resultado, dict):
            cargado = {True: "cargado", False: "se carga al usarlo", None: "LM Studio no responde"}[resultado["loaded"]]
            self._estado.setText(f"Modelo: {resultado['model']} ({cargado})")
            return
        self._pintar()

    def al_evento(self, ev: dict) -> None:
        """Eventos de tasks-service reenviados por el controlador."""
        if ev.get("type") in ("task.created", "task.updated"):
            t = ev["task"]
            self.tareas[t["id"]] = t
            if self.isVisible():
                self._pintar()
        elif ev.get("type") == "extraction.updated":
            ex = ev["extraction"]
            if ex["status"] == "en_cola" and ex.get("waiting"):
                self.avisar(f"Tareas en espera: {ex['waiting']}. Se harán solas cuando se libere")
            elif ex["status"] == "procesando":
                self.avisar("Buscando tareas en un dictado…")
            elif ex["status"] == "lista":
                self.avisar(f"{ex['tasks']} tareas propuestas en {ex['duration_s'] or 0:.0f} s" if ex["tasks"]
                            else "No se encontraron tareas en el dictado")
            elif ex["status"] == "error":
                self.avisar(f"No se pudieron extraer las tareas: {ex['error']}", error=True)

    def avisar(self, texto: str, error: bool = False) -> None:
        self._estado.setText(texto)
        self._estado.setStyleSheet(f"color: {'#B91C1C' if error else '#6B7280'};")

    # ------------------------------------------------------------ vistas

    def _de(self, estado: str, categoria: str | None = None) -> list[dict]:
        return [t for t in self.tareas.values()
                if t["status"] == estado and (categoria is None or t["category"] == categoria)]

    def _nombres_categorias(self) -> list[str]:
        nombres = [c["name"] for c in self.categorias]
        extra = sorted({t["category"] for t in self.tareas.values() if t["status"] in ("pendiente", "propuesta")}
                       - set(nombres))
        return nombres + extra

    def _pintar(self) -> None:
        self._llenando = True
        propuestas = len(self._de("propuesta"))
        entradas = [(PROPUESTAS, f"Propuestas ({propuestas})" if propuestas else "Propuestas")]
        entradas += [(f"cat:{n}", f"{n} ({len(self._de('pendiente', n))})") for n in self._nombres_categorias()]
        entradas.append((HECHAS, "Hechas"))
        self._vistas.clear()
        for clave, texto in entradas:
            item = QListWidgetItem(texto)
            item.setData(Qt.ItemDataRole.UserRole, clave)
            if clave == PROPUESTAS and propuestas:
                fuente = item.font()
                fuente.setBold(True)
                item.setFont(fuente)
            self._vistas.addItem(item)
            if clave == self._vista:
                self._vistas.setCurrentItem(item)
        if self._vistas.currentItem() is None:
            self._vista = PROPUESTAS
            self._vistas.setCurrentRow(0)
        self._llenando = False
        self._pintar_lista()

    def _al_elegir_vista(self, actual: QListWidgetItem | None, anterior: QListWidgetItem | None) -> None:
        if self._llenando or actual is None:
            return
        self._vista = actual.data(Qt.ItemDataRole.UserRole)
        self._pintar_lista(avisar_vacia=True)

    def _pintar_lista(self, avisar_vacia: bool = False) -> None:
        """`avisar_vacia` solo al cambiar de vista: al repintar por un evento no se tapa el aviso de la acción."""
        vista, hoy = self._vista, date.today()
        if vista == PROPUESTAS:
            tareas = sorted(self._de("propuesta"), key=lambda t: t["created_at"], reverse=True)
            self._cabecera.setText("Propuestas")
            self._descripcion.setText("Lo que el organizador sacó de tus dictados. Apruébalas, corrígelas o descártalas.")
        elif vista == HECHAS:
            tareas = sorted(self._de("hecha"), key=lambda t: t.get("done_at") or "", reverse=True)
            self._cabecera.setText("Hechas")
            self._descripcion.setText("Desmarca una tarea para devolverla a su lista.")
        else:
            nombre = vista.removeprefix("cat:")
            tareas = sorted(self._de("pendiente", nombre),
                            key=lambda t: (t["due"] is None, t["due"] or "", t["created_at"]))
            self._cabecera.setText(nombre)
            self._descripcion.setText(next((c["description"] for c in self.categorias if c["name"] == nombre), ""))
        self._llenando = True
        self._lista.clear()
        for t in tareas:
            item = QListWidgetItem(rotulo(t, "propuestas" if vista == PROPUESTAS else
                                          HECHAS if vista == HECHAS else "categoria", hoy))
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            if vista != PROPUESTAS:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if t["status"] == "hecha" else Qt.CheckState.Unchecked)
            if vencida(t, hoy):
                item.setForeground(QBrush(ROJO))
            elif vista == HECHAS:
                item.setForeground(QBrush(GRIS))
            self._lista.addItem(item)
        self._llenando = False
        en_categoria = vista.startswith("cat:")
        self._nueva.setVisible(en_categoria)
        self._nueva.setPlaceholderText(f"Añadir una tarea en {vista.removeprefix('cat:')} y pulsar Enter"
                                       if en_categoria else "")
        if not tareas and avisar_vacia:
            self.avisar({PROPUESTAS: "No hay propuestas pendientes de revisar",
                         HECHAS: "Todavía no has marcado ninguna tarea como hecha"}.get(vista, "Nada pendiente aquí"))
        self._habilitar()

    def _habilitar(self) -> None:
        vista, hay = self._vista, bool(self._lista.selectedItems())
        visibles = {PROPUESTAS: ("aprobar", "todas", "editar", "descartar", "dictado"),
                    HECHAS: ("reabrir", "dictado")}.get(vista, ("editar", "descartar", "dictado"))
        for clave, b in self._botones.items():
            b.setVisible(clave in visibles)
            b.setEnabled(clave == "todas" and self._lista.count() > 0 or hay)
        if hay and len(self._seleccion()) == 1:
            self._botones["dictado"].setEnabled(bool(self._seleccion()[0].get("session_id")))
        else:
            self._botones["dictado"].setEnabled(False)

    # ------------------------------------------------------------ acciones

    def _seleccion(self) -> list[dict]:
        return [self.tareas[i.data(Qt.ItemDataRole.UserRole)] for i in self._lista.selectedItems()]

    def _cambiar(self, tarea: dict, cambios: dict[str, Any]) -> None:
        self._cliente.pedir("tareas:editar", "PATCH", f"/tasks/{tarea['id']}", cambios)

    def aprobar(self) -> None:
        self._aprobar(self._seleccion())

    def aprobar_todas(self) -> None:
        self._aprobar(self._de("propuesta"))

    def _aprobar(self, tareas: list[dict]) -> None:
        for t in tareas:
            self._cambiar(t, {"status": "pendiente"})
        if tareas:
            self.avisar(f"Aprobada{'s' if len(tareas) != 1 else ''}, en sus listas: {resumen_categorias(tareas)}")

    def descartar(self) -> None:
        for t in self._seleccion():
            self._cambiar(t, {"status": "descartada"})

    def reabrir(self) -> None:
        for t in self._seleccion():
            self._cambiar(t, {"status": "pendiente"})

    def editar(self) -> None:
        seleccion = self._seleccion()
        if len(seleccion) != 1:
            return
        dialogo = DialogoTarea(seleccion[0], [c["name"] for c in self.categorias if c.get("configured", True)], self)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            cambios = dialogo.cambios()
            if cambios["title"]:
                self._cambiar(seleccion[0], cambios)

    def abrir_dictado(self) -> None:
        seleccion = self._seleccion()
        if len(seleccion) == 1 and seleccion[0].get("session_id"):
            self.ver_dictado.emit(seleccion[0]["session_id"])

    def anadir(self) -> None:
        titulo = self._nueva.text().strip()
        if not titulo or not self._vista.startswith("cat:"):
            return
        self._cliente.pedir("tareas:crear", "POST", "/tasks",
                            {"title": titulo, "category": self._vista.removeprefix("cat:")})
        self._nueva.clear()

    def _al_marcar(self, item: QListWidgetItem) -> None:
        if self._llenando or self._vista == PROPUESTAS:
            return
        t = self.tareas[item.data(Qt.ItemDataRole.UserRole)]
        hecha = item.checkState() == Qt.CheckState.Checked
        if hecha != (t["status"] == "hecha"):
            self._cambiar(t, {"status": "hecha" if hecha else "pendiente"})
