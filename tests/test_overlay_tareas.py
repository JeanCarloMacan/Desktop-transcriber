"""F4: ventana de tareas y botón «Extraer tareas» del editor, en plataforma offscreen con un cliente falso."""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402

from overlay.editor import Editor  # noqa: E402
from overlay.tareas import (PROPUESTAS, VentanaTareas, fecha_corta, resumen_categorias, rotulo,  # noqa: E402
                            vencida)

HOY = date(2026, 10, 21)


@pytest.fixture(scope="module")
def app() -> Iterator[object]:
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class ClienteFalso(QObject):
    respuesta = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.pedidos: list[tuple[str, str, str, dict | None]] = []

    def pedir(self, operacion: str, metodo: str, ruta: str, cuerpo: dict | None = None) -> None:
        self.pedidos.append((operacion, metodo, ruta, cuerpo))


def t(id_: int, title: str, status: str = "pendiente", category: str = "Trabajo", **extra: object) -> dict:
    return {"id": id_, "title": title, "category": category, "status": status, "due": None, "notes": None,
            "session_id": "s1", "extraction_id": 1, "quote": None, "created_at": f"2026-10-21T20:00:0{id_}+00:00",
            "updated_at": "2026-10-21T20:00:00+00:00", "done_at": None, **extra}


CATS = [{"name": "Trabajo", "description": "proyectos", "pending": 1, "proposed": 2, "configured": True},
        {"name": "Personal", "description": "casa", "pending": 0, "proposed": 0, "configured": True},
        {"name": "Otros", "description": "lo demás", "pending": 0, "proposed": 0, "configured": True}]


def abierta() -> tuple[VentanaTareas, ClienteFalso]:
    cliente = ClienteFalso()
    v = VentanaTareas(cliente)  # type: ignore[arg-type]
    v.refrescar()
    assert [p[2] for p in cliente.pedidos] == ["/categories", "/tasks?status=propuesta", "/tasks?status=pendiente",
                                                "/tasks?status=hecha&limit=200", "/llm"]
    cliente.respuesta.emit("tareas:categorias", CATS)
    cliente.respuesta.emit("tareas:lista", [t(1, "Enviar el informe", "propuesta", quote="mandarle el informe"),
                                            t(2, "Comprar pilas", "propuesta", "Personal")])
    cliente.respuesta.emit("tareas:lista", [t(3, "Subir el código", due="2026-10-20")])
    cliente.respuesta.emit("tareas:lista", [])
    cliente.pedidos.clear()
    return v, cliente


def textos(lista: QtWidgets.QListWidget) -> list[str]:
    return [lista.item(i).text().split("\n")[0] for i in range(lista.count())]


def test_vistas_con_recuentos_y_propuestas_primero(app: object) -> None:
    v, _ = abierta()
    assert textos(v._vistas) == ["Propuestas (2)", "Trabajo (1)", "Personal (0)", "Otros (0)", "Hechas"]
    assert v._vistas.item(0).font().bold()
    assert textos(v._lista) == ["Comprar pilas", "Enviar el informe"]  # las más recientes arriba
    assert "«mandarle el informe»" in v._lista.item(1).text()


def test_aprobar_las_seleccionadas_y_todas(app: object) -> None:
    v, cliente = abierta()
    v._lista.item(1).setSelected(True)
    v.aprobar()
    assert cliente.pedidos == [("tareas:editar", "PATCH", "/tasks/1", {"status": "pendiente"})]
    cliente.pedidos.clear()
    v.show()
    v.aprobar_todas()
    assert sorted(p[2] for p in cliente.pedidos) == ["/tasks/1", "/tasks/2"]
    assert v._estado.text() == "Aprobadas, en sus listas: Trabajo (1), Personal (1)"
    for id_, titulo, cat in [(1, "Enviar el informe", "Trabajo"), (2, "Comprar pilas", "Personal")]:
        v.al_evento({"type": "task.updated", "task": t(id_, titulo, "pendiente", cat)})
    assert textos(v._vistas)[:3] == ["Propuestas", "Trabajo (2)", "Personal (1)"]
    assert v._estado.text().startswith("Aprobadas")  # el repintado por los eventos no tapa el aviso
    v.hide()


def test_los_eventos_mueven_la_tarea_de_vista(app: object) -> None:
    v, _ = abierta()
    v.show()
    v.al_evento({"type": "task.updated", "task": t(1, "Enviar el informe", "pendiente")})
    assert textos(v._vistas)[:2] == ["Propuestas (1)", "Trabajo (2)"]
    v.al_evento({"type": "extraction.updated", "extraction": {"status": "lista", "tasks": 3, "duration_s": 4.2}})
    assert "3 tareas propuestas" in v._estado.text()
    v.al_evento({"type": "extraction.updated",
                 "extraction": {"status": "en_cola", "waiting": "la RTX 5090 está ocupada (3,2 GB en uso)"}})
    assert v._estado.text().startswith("Tareas en espera: la RTX 5090 está ocupada")
    v.hide()


def test_marcar_hecha_y_anadir_en_una_categoria(app: object) -> None:
    v, cliente = abierta()
    v._vistas.setCurrentRow(1)  # Trabajo
    assert textos(v._lista) == ["Subir el código"] and v._nueva.isVisibleTo(v)
    item = v._lista.item(0)
    assert item.foreground().color().name() == "#b91c1c"  # vencida
    item.setCheckState(Qt.CheckState.Checked)
    assert cliente.pedidos[-1] == ("tareas:editar", "PATCH", "/tasks/3", {"status": "hecha"})
    v._nueva.setText("Revisar el PR de Laura")
    v.anadir()
    assert cliente.pedidos[-1] == ("tareas:crear", "POST", "/tasks",
                                   {"title": "Revisar el PR de Laura", "category": "Trabajo"})
    v._vistas.setCurrentRow(0)
    assert v._vista == PROPUESTAS and not v._nueva.isVisibleTo(v)


def test_rotulos_y_fechas_cortas() -> None:
    assert [fecha_corta(d, HOY) for d in ("2026-10-21", "2026-10-22", "2026-10-23", "2027-01-05")] == \
        ["hoy", "mañana", "vie 23 oct", "mar 5 ene 2027"]
    pendiente = t(1, "Pagar la luz", category="Personal", due="2026-10-20")
    assert vencida(pendiente, HOY) and not vencida({**pendiente, "status": "hecha"}, HOY)
    assert rotulo(pendiente, "categoria", HOY) == "Pagar la luz\nvenció mar 20 oct"
    assert rotulo({**pendiente, "status": "propuesta", "due": None, "quote": "pagar la luz"}, PROPUESTAS, HOY) == \
        "Pagar la luz\nPersonal · «pagar la luz»"


def test_extraer_tareas_desde_el_editor_guarda_antes_si_hay_cambios(app: object, tmp_path: object) -> None:
    cliente = ClienteFalso()
    editor = Editor(cliente, con_tareas=True)  # type: ignore[arg-type]
    pedidas: list[str] = []
    editor.extraer_tareas.connect(pedidas.append)
    editor._llenar_lista([{"id": "s1", "started_at": "2026-10-21T21:40:00+00:00", "mode": "tarea",
                           "text": "Comprar pan", "attachments": 0, "edited_at": None}])
    cliente.respuesta.emit("editor:doc", {"session_id": "s1", "markdown": "# Dictado\n\nComprar pan.\n",
                                          "edited": False, "edited_at": None, "folder": str(tmp_path), "images": 0})
    assert editor._botones["tareas"].isEnabled()
    editor.extraer()
    assert pedidas == ["s1"]
    editor._texto.append("Y llamar al banco.")
    editor.extraer()
    assert pedidas == ["s1"] and cliente.pedidos[-1][0] == "editor:guardar"  # primero guarda…
    cliente.respuesta.emit("editor:guardar", {"session_id": "s1"})
    assert pedidas == ["s1", "s1"]  # …y extrae cuando el guardado se confirma


def test_resumen_de_categorias_al_aprobar() -> None:
    assert resumen_categorias([t(1, "a"), t(2, "b", category="Personal"), t(3, "c")]) == "Trabajo (2), Personal (1)"
    assert resumen_categorias([]) == ""
