"""Editor de dictados y capturador, con plataforma offscreen y un cliente falso (sin voice-core)."""
from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

from overlay.capturas import Capturador  # noqa: E402
from overlay.editor import Editor  # noqa: E402


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


def carpeta_con_captura(tmp_path: Path, n: int = 1) -> Path:
    carpeta = tmp_path / "sesion uno"
    (carpeta / "capturas").mkdir(parents=True)
    for i in range(1, n + 1):
        img = QImage(1600, 900, QImage.Format.Format_RGB32)
        img.fill(QColor("steelblue"))
        img.save(str(carpeta / "capturas" / f"{i:03d}.png"))
    return carpeta


def documento(carpeta: Path, editado: bool = False) -> dict:
    md = ("# Dictado · 2026-10-21 18:40\n\nRevisa la figura 3: la leyenda tapa la curva.\n\n"
          "![Imagen 1](capturas/001.png)\n\nAdemás, el eje X no coincide.\n")
    return {"session_id": "s1", "markdown": md, "edited": editado, "edited_at": None, "folder": str(carpeta),
            "images": 1}


def abierto(tmp_path: Path, n: int = 1) -> tuple[Editor, ClienteFalso, Path]:
    cliente = ClienteFalso()
    editor = Editor(cliente)  # type: ignore[arg-type]
    carpeta = carpeta_con_captura(tmp_path, n)
    editor._llenar_lista([{"id": "s1", "started_at": "2026-10-21T21:40:00+00:00", "mode": "dictado",
                           "text": "Revisa la figura 3…", "attachments": 1, "edited_at": None}])
    assert cliente.pedidos[-1][:3] == ("editor:doc", "GET", "/sessions/s1/document")
    cliente.respuesta.emit("editor:doc", documento(carpeta))
    return editor, cliente, carpeta


def test_el_editor_muestra_el_documento_con_la_imagen_en_su_sitio(app: object, tmp_path: Path) -> None:
    editor, _, carpeta = abierto(tmp_path)
    md = editor.markdown()
    assert "![Imagen 1](capturas/001.png)" in md and md.index("leyenda") < md.index("![Imagen 1]") < md.index("eje X")
    assert editor._arrastre.rutas == [carpeta / "capturas" / "001.png"]
    assert not editor._modificado and not editor._botones["original"].isEnabled()


def test_editar_y_guardar_manda_markdown_sin_cortes(app: object, tmp_path: Path) -> None:
    editor, cliente, _ = abierto(tmp_path)
    editor._texto.append("Una frase añadida a mano que es lo bastante larga como para que el editor la parta al "
                         "exportarla en varias líneas de ochenta columnas.")
    assert editor._modificado
    editor.guardar()
    operacion, metodo, ruta, cuerpo = cliente.pedidos[-1]
    assert (operacion, metodo, ruta) == ("editor:guardar", "PUT", "/sessions/s1/document")
    assert "parta al exportarla en varias líneas" in cuerpo["markdown"]  # re-unida en un solo párrafo
    assert "![Imagen 1](capturas/001.png)" in cuerpo["markdown"]


def test_copiar_para_chat_y_para_agente(app: object, tmp_path: Path) -> None:
    editor, _, carpeta = abierto(tmp_path)
    editor.copiar_para_chat()
    chat = QGuiApplication.clipboard().text()
    assert chat.startswith("Revisa la figura 3") and "[Imagen 1]" in chat and "#" not in chat
    editor.copiar_para_agente()
    agente = QGuiApplication.clipboard().text()
    assert f"(<{(carpeta / 'capturas' / '001.png').as_posix()}>)" in agente


def test_insertar_captura_en_el_cursor(app: object, tmp_path: Path) -> None:
    editor, _, carpeta = abierto(tmp_path, n=2)
    pedidas: list[str] = []
    editor.capturar.connect(pedidas.append)
    editor.insertar_captura()
    assert pedidas == ["s1"] and editor._insertando
    editor.insertar_imagen({"session_id": "s1", "file": "capturas/002.png"})  # cursor en el título
    assert not editor._insertando and editor._modificado
    md = editor.markdown()
    assert md.count("![") == 2 and "# #" not in md  # no hereda el formato del título
    assert md.index("capturas/002.png") < md.index("Revisa")  # justo tras el párrafo del cursor
    assert editor._arrastre.rutas == [carpeta / "capturas" / "002.png", carpeta / "capturas" / "001.png"]


def test_las_imagenes_se_escalan_al_ancho_de_la_ventana(app: object, tmp_path: Path) -> None:
    editor, _, _ = abierto(tmp_path)
    editor.resize(700, 600)
    editor._escalar_imagenes()
    doc = editor._texto.document()
    texto = doc.toPlainText()
    from PySide6.QtGui import QTextCursor

    cursor = QTextCursor(doc)
    pos = texto.index("￼")
    cursor.setPosition(pos)
    cursor.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
    assert 0 < cursor.charFormat().toImageFormat().width() < 1600


def esperar_eventos(condicion, limite_s: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin and not condicion():
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def test_el_capturador_toma_la_imagen_del_portapapeles(app: object) -> None:
    lanzados: list[int] = []
    capt = Capturador(lanzar=lambda: lanzados.append(1))
    listas: list[tuple[bytes, dict]] = []
    terminadas: list[bool] = []
    capt.lista.connect(lambda png, ctx: listas.append((png, ctx)))
    capt.terminada.connect(terminadas.append)
    assert capt.capturar({"sesion": "s1", "t": 3.5}) and lanzados == [1]
    assert not capt.capturar({"sesion": "s1"})  # una a la vez
    QGuiApplication.clipboard().setText("texto: no cuenta")
    esperar_eventos(lambda: False, 0.1)
    assert listas == []
    img = QImage(40, 20, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    QGuiApplication.clipboard().setImage(img)
    esperar_eventos(lambda: bool(listas))
    png, contexto = listas[0]
    assert png.startswith(b"\x89PNG") and contexto == {"sesion": "s1", "t": 3.5} and terminadas == [True]


def test_el_capturador_se_rinde_si_se_cancela_el_recorte(app: object) -> None:
    capt = Capturador(lanzar=lambda: None)
    capt._espera.setInterval(50)
    terminadas: list[bool] = []
    capt.terminada.connect(terminadas.append)
    capt.capturar({"sesion": "s1"})
    esperar_eventos(lambda: bool(terminadas))
    assert terminadas == [False] and not capt.esperando
