"""Compara las hipótesis de cada configuración de una corrida de bench.py, con foco en la jerga técnica.

Uso (desde la raíz):  .venv\\Scripts\\python spikes\\f0\\comparar.py datos\\f0\\resultados\\<corrida>
Escribe <corrida>\\comparacion.md y muestra el resumen.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import comun

log = logging.getLogger("comparar")
AQUI = Path(__file__).resolve().parent


def terminos_glosario() -> list[str]:
    texto = (AQUI / "glosario.txt").read_text(encoding="utf-8-sig").strip()
    texto = texto.split(":", 1)[1] if ":" in texto else texto
    return [t.strip().rstrip(".") for t in re.split(r",\s*|\s+y\s+", texto) if t.strip()]


def contiene(palabras: list[str], termino: list[str]) -> bool:
    n = len(termino)
    return any(palabras[i:i + n] == termino for i in range(len(palabras) - n + 1))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) != 2:
        log.error(__doc__)
        return 2
    corrida = Path(sys.argv[1]).resolve()
    resultados = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(corrida.glob("*.json"))
                  if p.name != "clips.json"]
    terminos = {t: comun.normalizar(t) for t in terminos_glosario()}

    lineas = [f"# Comparación · {corrida.name}", "", "## Jerga reconocida", "",
              "| Config | WER | CER | Términos acertados | Términos fallados |", "|---|---|---|---|---|"]
    detalle = ["", "## Por clip", ""]
    por_clip: dict[str, dict[str, str]] = {}
    for r in resultados:
        aciertos, fallos = 0, []
        for c in r["clips"]:
            por_clip.setdefault(c["id"], {"_ref": c.get("referencia") or ""})[r["config"]] = c["hipotesis"]
            if not c.get("referencia"):
                continue
            ref, hip = comun.normalizar(c["referencia"]), comun.normalizar(c["hipotesis"])
            for termino, norm in terminos.items():
                if contiene(ref, norm):
                    if contiene(hip, norm):
                        aciertos += 1
                    else:
                        fallos.append(termino)
        total = aciertos + len(fallos)
        wer = "—" if r["wer"] is None else f"{r['wer']:.1%}"
        cer = "—" if r["cer"] is None else f"{r['cer']:.1%}"
        lineas.append(f"| {r['config']} | {wer} | {cer} | {aciertos}/{total} | {', '.join(fallos) or '—'} |")

    for clip, hips in por_clip.items():
        detalle.append(f"### {clip}")
        detalle.append(f"- **Referencia:** {hips.pop('_ref') or '(toma libre, sin referencia)'}")
        for cfg, hip in hips.items():
            detalle.append(f"- `{cfg}`: {hip}")
        detalle.append("")

    md = "\n".join(lineas + detalle)
    (corrida / "comparacion.md").write_text(md + "\n", encoding="utf-8")
    log.info("\n".join(lineas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
