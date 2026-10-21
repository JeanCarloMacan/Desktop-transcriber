"""Genera docs/img/panel.png: el panel real (Qt, fuentes de Windows) en tres estados, sin mostrarlo en pantalla.

Uso (desde la raíz):  .venv\\Scripts\\python scripts\\capturar_panel.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from overlay.estado import EstadoPanel
from overlay.panel import Panel

RAIZ = Path(__file__).resolve().parents[1]
FRASE_1 = ("Revisa la figura 3: la leyenda tapa la curva del modelo y el eje vertical no tiene unidades. "
           "Además, en el endpoint de sesiones falta validar el payload con Pydantic.")
FRASE_2 = "Sube un poco el learning rate y guarda un checkpoint en cada época."
INICIO = datetime.now(UTC) - timedelta(seconds=42)


def ev(tipo: str, **datos: object) -> dict:
    return {"type": tipo, "ts": INICIO.isoformat(), "seq": None, **datos}


def dictando() -> EstadoPanel:
    e = EstadoPanel()
    e.fijar_conexion(True)
    e.aplicar(ev("engine.state", state="dictando", mode="auto", device="cuda", reason=""))
    e.aplicar(ev("session.started", session_id="demo", mode="dictado"))
    e.aplicar(ev("segment.final", session_id="demo", index=0, t0=0, t1=9, text=FRASE_1))
    e.aplicar(ev("segment.partial", session_id="demo", text="Sube un poco el learning rate y"))
    return e


def terminado() -> EstadoPanel:
    e = dictando()
    e.aplicar(ev("session.finalized", session_id="demo", duration_s=42, segments=2, text=f"{FRASE_1} {FRASE_2}"))
    e.aplicar(ev("engine.state", state="en_espera", mode="auto", device="cuda", reason=""))
    e.fijar_motor({"state": "en_espera", "mode": "auto", "device": "cuda", "rest_in_s": 105, "auto_off_in_s": 1905}, 0)
    return e


def reposo() -> EstadoPanel:
    e = terminado()
    e.aplicar(ev("engine.state", state="en_reposo", mode="auto", device="cuda", reason=""))
    e.fijar_motor({"state": "en_reposo", "mode": "auto", "device": "cuda", "rest_in_s": None, "auto_off_in_s": 1690}, 0)
    e.aviso = ""
    return e


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  (Qt necesita la aplicación viva)
    carpeta = Path(tempfile.mkdtemp())
    rotulos = ["Dictando: texto final en blanco, provisional en gris",
               "Al terminar: copiado al portapapeles; Ctrl+Alt+V lo pega",
               "En reposo: el modelo en RAM, vuelve a la GPU en 0,4 s"]
    capturas = []
    for i, estado in enumerate([dictando(), terminado(), reposo()]):
        panel = Panel(680, 13, 0.94, carpeta / f"{i}.json")
        panel.actualizar(estado, 0)
        capturas.append(panel.grab().toImage())
    margen, rotulo = 24, 30
    alto = sum(c.height() for c in capturas) + len(capturas) * (rotulo + margen) + margen
    lienzo = QImage(680 + 2 * margen, alto, QImage.Format.Format_ARGB32)
    lienzo.fill(QColor("#E5E7EB"))
    p = QPainter(lienzo)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(QFont("Segoe UI", 10))
    y = margen
    for texto, captura in zip(rotulos, capturas):
        p.setPen(QColor("#374151"))
        p.drawText(margen, y, 680, rotulo, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, texto)
        y += rotulo
        p.drawImage(margen, y, captura)
        y += captura.height() + margen
    p.end()
    destino = RAIZ / "docs" / "img" / "panel.png"
    destino.parent.mkdir(parents=True, exist_ok=True)
    lienzo.save(str(destino))
    sys.stdout.write(f"escrito {destino} ({lienzo.width()}x{lienzo.height()})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
