"""Genera contracts/openapi.json y contracts/eventos.schema.json a partir de src/voice_core/contratos.py.

Uso (desde la raíz):  .venv\\Scripts\\python scripts\\exportar_contratos.py [--comprobar]
Con --comprobar no escribe nada y sale con código 1 si los archivos están desactualizados.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
CONTRATOS = RAIZ / "contracts"


def generar() -> dict[str, str]:
    from voice_core.api import crear_app
    from voice_core.contratos import EVENTO

    app = crear_app(None)  # type: ignore[arg-type]  # el esquema no necesita servicios
    eventos = EVENTO.json_schema()
    eventos["title"] = "Eventos de voice-core"
    return {
        "openapi.json": json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        "eventos.schema.json": json.dumps(eventos, ensure_ascii=False, indent=2) + "\n",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comprobar", action="store_true")
    a = ap.parse_args()
    archivos = generar()
    if a.comprobar:
        distintos = [n for n, t in archivos.items()
                     if not (CONTRATOS / n).is_file() or (CONTRATOS / n).read_text(encoding="utf-8") != t]
        if distintos:
            sys.stderr.write(f"Contratos desactualizados: {', '.join(distintos)}\n")
            return 1
        return 0
    CONTRATOS.mkdir(exist_ok=True)
    for nombre, texto in archivos.items():
        (CONTRATOS / nombre).write_text(texto, encoding="utf-8")
        sys.stdout.write(f"escrito {CONTRATOS / nombre}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
