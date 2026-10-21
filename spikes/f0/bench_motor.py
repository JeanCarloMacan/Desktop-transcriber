"""Mide UNA configuración del motor en su propio proceso y escribe un JSON. Lo lanza bench.py.

El proceso debe arrancar con CUDA_VISIBLE_DEVICES = UUID de la 5080 (modo cuda) o "-1" (modo cpu).
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

import comun
import entorno

log = logging.getLogger("bench_motor")


def args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--modelo", required=True, help="nombre corto (large-v3, large-v3-turbo) o carpeta")
    ap.add_argument("--dispositivo", choices=["cuda", "cpu"], required=True)
    ap.add_argument("--compute", required=True)
    ap.add_argument("--hilos", type=int, default=0)
    ap.add_argument("--clips", type=Path, required=True)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--glosario", default="", help="texto para initial_prompt (vacío = sin glosario)")
    return ap.parse_args()


def transcribir(modelo, audio: np.ndarray, beam: int, prompt: str | None, sin_tiempos: bool = False) -> tuple[str, float]:
    t = time.perf_counter()
    segs, _ = modelo.transcribe(audio, language="es", beam_size=beam, vad_filter=False,
                                condition_on_previous_text=False, initial_prompt=prompt,
                                without_timestamps=sin_tiempos)
    texto = " ".join(s.text.strip() for s in segs).strip()
    return texto, time.perf_counter() - t


def main() -> int:
    a = args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    visibles = os.environ.get("CUDA_VISIBLE_DEVICES")
    if a.dispositivo == "cuda" and visibles != entorno.UUID_5080:
        log.error("Modo cuda exige CUDA_VISIBLE_DEVICES = UUID de la 5080 (es %r)", visibles)
        return 2
    if a.dispositivo == "cpu" and visibles != "-1":
        log.error("Modo cpu exige CUDA_VISIBLE_DEVICES = -1 (es %r)", visibles)
        return 2
    entorno.preparar_cuda()

    import ctranslate2
    from faster_whisper import WhisperModel

    if a.dispositivo == "cuda" and ctranslate2.get_cuda_device_count() != 1:
        log.error("Se esperaba 1 GPU visible y hay %d", ctranslate2.get_cuda_device_count())
        return 3

    carpeta = Path(a.modelo) if Path(a.modelo).is_dir() else entorno.carpeta_modelo(a.modelo)
    clips = comun.leer_clips(a.clips)
    prompt = a.glosario or None

    vram_base = entorno.vram_usada_mib()
    t = time.perf_counter()
    modelo = WhisperModel(str(carpeta), device=a.dispositivo, device_index=0, compute_type=a.compute,
                          cpu_threads=a.hilos)
    carga_s = time.perf_counter() - t
    vram_carga = entorno.vram_usada_mib()

    ruido = (np.random.default_rng(0).standard_normal(comun.SR) * 0.005).astype(np.float32)
    _, frio_s = transcribir(modelo, ruido, beam=1, prompt=None)
    _, tibio_s = transcribir(modelo, ruido, beam=1, prompt=None)
    log.info("[%s] carga %.1f s · 1.ª llamada %.2f s · 2.ª %.2f s · VRAM +%d MiB",
             a.config, carga_s, frio_s, tibio_s, vram_carga - vram_base)

    filas, vram_pico = [], vram_carga
    err_p = tot_p = err_c = tot_c = 0
    for c in clips:
        audio = comun.cargar_audio_16k(Path(c.wav))
        dur = len(audio) / comun.SR
        hip, t_final = transcribir(modelo, audio, beam=5, prompt=prompt)
        parciales = {}
        for ventana in (2, 4):
            if dur >= ventana:
                _, parciales[f"parcial_{ventana}s"] = transcribir(
                    modelo, audio[: ventana * comun.SR], beam=1, prompt=prompt, sin_tiempos=True)
        fila = {"id": c.id, "dur_s": round(dur, 2), "final_s": round(t_final, 3), "rtf": round(t_final / dur, 4),
                "hipotesis": hip, "referencia": c.referencia,
                **{k: round(v, 3) for k, v in parciales.items()}}
        if c.referencia:
            ep, tp = comun.errores_palabras(c.referencia, hip)
            ec, tc = comun.errores_caracteres(c.referencia, hip)
            fila |= {"wer": round(ep / tp, 4), "cer": round(ec / tc, 4)}
            err_p, tot_p, err_c, tot_c = err_p + ep, tot_p + tp, err_c + ec, tot_c + tc
        filas.append(fila)
        vram_pico = max(vram_pico, entorno.vram_usada_mib())
        log.info("[%s] %-22s %5.1f s audio → %.2f s  WER %s", a.config, c.id, dur, t_final, fila.get("wer", "—"))

    del modelo
    gc.collect()
    time.sleep(1.0)
    vram_borrado = entorno.vram_usada_mib()

    def media(clave: str) -> float | None:
        vals = [f[clave] for f in filas if clave in f]
        return round(sum(vals) / len(vals), 3) if vals else None

    resultado = {
        "config": a.config, "modelo": a.modelo, "dispositivo": a.dispositivo, "compute": a.compute,
        "hilos": a.hilos, "glosario": bool(prompt),
        "carga_s": round(carga_s, 2), "frio_s": round(frio_s, 2), "tibio_s": round(tibio_s, 3),
        "vram_base_mib": vram_base, "vram_carga_mib": vram_carga - vram_base,
        "vram_pico_mib": vram_pico - vram_base, "vram_tras_borrar_mib": vram_borrado - vram_base,
        "wer": round(err_p / tot_p, 4) if tot_p else None, "cer": round(err_c / tot_c, 4) if tot_c else None,
        "rtf_medio": media("rtf"), "parcial_2s_medio": media("parcial_2s"), "parcial_4s_medio": media("parcial_4s"),
        "clips": filas,
    }
    a.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
