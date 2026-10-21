"""El panel de Qt se construye y dibuja el estado sin mostrarse en pantalla (plataforma offscreen)."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint  # noqa: E402

from overlay.estado import EstadoPanel  # noqa: E402
from overlay.panel import LETRA_MAX, Panel  # noqa: E402

T0 = datetime(2026, 10, 21, 17, 0, 0, tzinfo=UTC)
LARGO = " ".join(["La figura tres necesita leyenda y unidades en el eje vertical."] * 12)


@pytest.fixture(scope="module")
def app() -> Iterator[object]:
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def estado_dictando(texto: str, parcial: str) -> EstadoPanel:
    e = EstadoPanel()
    e.fijar_conexion(True)
    e.aplicar({"type": "engine.state", "ts": T0.isoformat(), "seq": 1, "state": "dictando", "mode": "auto",
               "device": "cuda", "reason": ""})
    e.aplicar({"type": "session.started", "ts": T0.isoformat(), "seq": 2, "session_id": "s1", "mode": "dictado"})
    e.aplicar({"type": "segment.final", "ts": T0.isoformat(), "seq": 3, "session_id": "s1", "index": 0, "t0": 0,
               "t1": 1, "text": texto})
    e.aplicar({"type": "segment.partial", "ts": T0.isoformat(), "seq": None, "session_id": "s1", "text": parcial})
    return e


def nuevo(tmp_path: Path, ancho: int = 680, letra: float = 13) -> Panel:
    return Panel(ancho, letra, 0.94, tmp_path / "overlay.json")


def test_el_alto_es_fijo_como_una_nota_y_la_vista_amplia_es_mas_alta(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel.actualizar(estado_dictando("Hola.", ""), 0)
    corto = panel.height()
    panel.actualizar(estado_dictando(LARGO, "y además revisar la escala"), 0)
    assert panel.height() == corto  # el texto que no cabe se desplaza; el panel no salta
    panel.alternar_tamano()
    assert panel.height() > corto
    panel.alternar_tamano()
    assert panel.height() == corto


def test_arrastrar_el_borde_superior_agranda_hacia_arriba(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel.actualizar(estado_dictando(LARGO, ""), 0)
    alto, lineas, abajo = panel.height(), panel.lineas, panel.y() + panel.height() - 1
    arriba = frozenset({"arr"})
    panel._empezar_redimension(arriba, QPoint(0, 0))
    panel.redimensionar(arriba, 0, -120)  # 120 px hacia arriba ≈ 6 líneas de 20 px
    assert panel.lineas >= lineas + 5 and panel.height() > alto
    assert panel.y() + panel.height() - 1 == abajo  # el borde inferior no se mueve
    panel.mouseReleaseEvent(None)
    assert json.loads((tmp_path / "overlay.json").read_text(encoding="utf-8"))["lineas"][0] == panel.lineas


def test_arrastrar_la_esquina_cambia_ancho_y_alto(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel.actualizar(estado_dictando(LARGO, ""), 0)
    ancho, lineas = panel.width(), panel.lineas
    esquina = frozenset({"aba", "der"})
    panel._empezar_redimension(esquina, QPoint(0, 0))
    panel.redimensionar(esquina, 50, 60)
    assert panel.width() == ancho + 50 and panel.lineas > lineas
    panel.redimensionar(esquina, 5000, 0)  # nunca más ancho que la pantalla (la offscreen mide 800 px)
    assert panel.width() == QtWidgets.QApplication.primaryScreen().availableGeometry().width() - 32


def test_la_rueda_deja_leer_el_principio_sin_que_el_texto_nuevo_te_arrastre(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel.actualizar(estado_dictando(LARGO, ""), 0)
    barra = panel._cuerpo.verticalScrollBar()
    assert barra.maximum() > 0 and barra.value() == barra.maximum()  # empieza mostrando el final
    barra.setValue(0)  # el usuario sube a leer el principio
    panel.actualizar(estado_dictando(LARGO + " Y una frase más al final.", "provisional"), 0)
    assert barra.value() == 0
    panel.desplazar_al_final()
    panel.actualizar(estado_dictando(LARGO + " Y otra más.", ""), 0)
    assert barra.value() == barra.maximum()  # si estás al final, sigue al texto nuevo


def test_ctrl_rueda_agranda_la_letra_y_el_panel(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel.actualizar(estado_dictando(LARGO, ""), 0)
    alto = panel.height()
    panel.cambiar_letra(+4)
    assert panel.height() > alto
    for _ in range(100):
        panel.cambiar_letra(+1)
    assert json.loads((tmp_path / "overlay.json").read_text(encoding="utf-8"))["letra"] == LETRA_MAX


def test_el_panel_recuerda_posicion_ancho_lineas_letra_y_modo(app: object, tmp_path: Path) -> None:
    panel = nuevo(tmp_path)
    panel._ancho = 900
    panel._lineas[False] = 7
    panel.cambiar_letra(+2)
    panel.alternar_tamano()
    datos = json.loads((tmp_path / "overlay.json").read_text(encoding="utf-8"))
    assert datos["amplio"] is True and datos["ancho"] == 900 and datos["letra"] == 14 and datos["lineas"][0] == 7
    otro = nuevo(tmp_path, ancho=500, letra=10)  # los valores guardados ganan a los de la configuración
    assert (otro._ancho, otro._letra, otro._amplio, otro._lineas[False]) == (900, 14, True, 7)
    assert otro.width() == 900


def test_posicion_fuera_de_pantalla_vuelve_a_la_esquina(app: object, tmp_path: Path) -> None:
    (tmp_path / "overlay.json").write_text('{"x": 99999, "abajo": 99999, "amplio": false}', encoding="utf-8")
    assert nuevo(tmp_path)._ancla is None


def test_captura_del_panel_para_revision_visual(app: object, tmp_path: Path) -> None:
    """Deja una captura en datos/f2/panel.png para revisarla a ojo (no se compara píxel a píxel)."""
    panel = nuevo(tmp_path)
    e = estado_dictando("La figura 3 necesita leyenda y unidades en el eje Y. Además, la escala del eje X no "
                        "coincide con la del texto.", "y habría que revisar los colores de")
    panel.actualizar(e, 0)
    imagen = panel.grab()
    assert imagen.width() == 680 and imagen.height() > 60
    destino = Path(__file__).resolve().parents[1] / "datos" / "f2" / "panel.png"
    destino.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(str(destino))
