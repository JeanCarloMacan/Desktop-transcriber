"""Orquesta el banco de pruebas F0: corre cada configuración en un proceso nuevo y resume los resultados.

Este proceso nunca carga CUDA: solo lee la VRAM por NVML para comprobar que las demás GPUs no se tocan
y que la del motor vuelve a su estado inicial al cerrar cada motor.

Uso (desde la raíz del proyecto):
    $env:TRT_F0_REFERENCIAS = "C:\\ruta\\a\\clips"   # carpeta con pares clip.wav + clip.txt (texto de referencia)
    .venv\\Scripts\\python spikes\\f0\\bench.py                       # esos clips de referencia
    .venv\\Scripts\\python spikes\\f0\\bench.py --set dictado         # tus grabaciones de datos\\f0\\audios
    .venv\\Scripts\\python spikes\\f0\\bench.py --configs turbo-gpu-int8,turbo-cpu-8
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import comun
import entorno

log = logging.getLogger("bench")

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
REFERENCIAS = os.environ.get("TRT_F0_REFERENCIAS", "")  # clips con texto de referencia (voz leída)
AUDIOS_DICTADO = RAIZ / "datos" / "f0" / "audios"

# id: (modelo, dispositivo, compute, hilos, glosario)
CONFIGS: dict[str, tuple[str, str, str, int, bool]] = {
    "v3-gpu-fp16": ("large-v3", "cuda", "float16", 0, False),
    "v3-gpu-int8": ("large-v3", "cuda", "int8_float16", 0, False),
    "turbo-gpu-fp16": ("large-v3-turbo", "cuda", "float16", 0, False),
    "turbo-gpu-int8": ("large-v3-turbo", "cuda", "int8_float16", 0, False),
    "turbo-gpu-int8+glosario": ("large-v3-turbo", "cuda", "int8_float16", 0, True),
    "v3-gpu-int8+glosario": ("large-v3", "cuda", "int8_float16", 0, True),
    "turbo-cpu-8": ("large-v3-turbo", "cpu", "int8", 8, False),
    "turbo-cpu-16": ("large-v3-turbo", "cpu", "int8", 16, False),
    "v3-cpu-8": ("large-v3", "cpu", "int8", 8, False),
}


def args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["referencias", "dictado", "todos"], default="referencias")
    ap.add_argument("--configs", default=",".join(CONFIGS), help="ids separados por coma")
    ap.add_argument("--excluir", default="", help="ids de clips a dejar fuera, separados por coma")
    ap.add_argument("--salida", type=Path, default=None)
    return ap.parse_args()


def reunir_clips(conjunto: str, excluir: set[str]) -> list[comun.Clip]:
    clips: list[comun.Clip] = []
    if conjunto in ("referencias", "todos"):
        if not REFERENCIAS:
            raise SystemExit("Define TRT_F0_REFERENCIAS con la carpeta de clips de referencia (wav + txt)")
        clips += [c for c in comun.clips_de_carpeta(Path(REFERENCIAS)) if c.referencia]
    if conjunto in ("dictado", "todos"):
        clips += comun.clips_de_carpeta(AUDIOS_DICTADO)
    return [c for c in clips if c.id not in excluir]


def correr(cid: str, clips_json: Path, salida: Path) -> dict | None:
    modelo, dispositivo, compute, hilos, con_glosario = CONFIGS[cid]
    if not entorno.modelo_presente(modelo):
        log.warning("[%s] modelo %s no está en %s; se omite", cid, modelo, entorno.carpeta_modelo(modelo))
        return None
    env = os.environ | {
        "CUDA_VISIBLE_DEVICES": entorno.UUID_5080 if dispositivo == "cuda" else "-1",
        "PYTHONIOENCODING": "utf-8",
    }
    destino = salida / f"{cid}.json"
    cmd = [sys.executable, str(AQUI / "bench_motor.py"), "--config", cid, "--modelo", modelo,
           "--dispositivo", dispositivo, "--compute", compute, "--hilos", str(hilos),
           "--clips", str(clips_json), "--salida", str(destino)]
    if con_glosario:
        cmd += ["--glosario", (AQUI / "glosario.txt").read_text(encoding="utf-8-sig").strip()]

    v5080, v5090 = entorno.vram_usada_mib(), entorno.vram_otras_mib()
    t = time.perf_counter()
    proc = subprocess.run(cmd, env=env, cwd=AQUI)
    total_s = time.perf_counter() - t
    time.sleep(1.5)
    v5080_fin, v5090_fin = entorno.vram_usada_mib(), entorno.vram_otras_mib()
    if proc.returncode != 0 or not destino.is_file():
        log.error("[%s] falló (código %d)", cid, proc.returncode)
        return None
    r = json.loads(destino.read_text(encoding="utf-8"))
    r |= {"proceso_total_s": round(total_s, 1), "vram_5080_tras_salir_mib": v5080_fin - v5080,
          "vram_5090_delta_mib": v5090_fin - v5090}
    destino.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return r


def tabla(resultados: list[dict]) -> str:
    def f(v: float | None, fmt: str) -> str:
        return "—" if v is None else format(v, fmt)

    filas = ["| Config | Carga s | 1.ª llamada s | VRAM MiB | Tras salir MiB | Δ 5090 MiB | WER | CER | RTF final "
             "| Parcial 2 s | Parcial 4 s |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in resultados:
        filas.append(
            f"| {r['config']} | {f(r['carga_s'], '.1f')} | {f(r['frio_s'], '.2f')} | {r['vram_pico_mib']} "
            f"| {r['vram_5080_tras_salir_mib']} | {r['vram_5090_delta_mib']} | {f(r['wer'], '.1%')} "
            f"| {f(r['cer'], '.1%')} | {f(r['rtf_medio'], '.3f')} | {f(r['parcial_2s_medio'], '.3f')} "
            f"| {f(r['parcial_4s_medio'], '.3f')} |")
    return "\n".join(filas)


def main() -> int:
    a = args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ids = [c.strip() for c in a.configs.split(",") if c.strip()]
    desconocidos = [c for c in ids if c not in CONFIGS]
    if desconocidos:
        log.error("Configs desconocidas: %s", desconocidos)
        return 2
    clips = reunir_clips(a.set, {c.strip() for c in a.excluir.split(",") if c.strip()})
    if not clips:
        log.error("No hay audios para el conjunto %r", a.set)
        return 2
    salida = (a.salida or RAIZ / "datos" / "f0" / "resultados" / f"{datetime.now():%Y%m%d-%H%M%S}-{a.set}").resolve()
    salida.mkdir(parents=True, exist_ok=True)
    clips_json = salida / "clips.json"
    comun.guardar_clips(clips, clips_json)
    log.info("%d clips (%s) · %d configs · resultados en %s", len(clips), a.set, len(ids), salida)

    resultados = [r for cid in ids if (r := correr(cid, clips_json, salida))]
    md = tabla(resultados)
    (salida / "resumen.md").write_text(md + "\n", encoding="utf-8")
    log.info("\n%s", md)
    return 0 if len(resultados) == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
