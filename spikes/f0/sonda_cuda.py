"""Comprueba que CTranslate2 corre en la GPU permitida con las DLLs del CUDA Toolkit, sin PyTorch.

Uso (desde la raíz del proyecto):  .venv\\Scripts\\python spikes\\f0\\sonda_cuda.py
(la GPU se elige con TRT_GPU, parte del nombre, por defecto «5080», o TRT_GPU_UUID)
"""
from __future__ import annotations

import logging
import os
import sys
import time

import numpy as np

import entorno

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("sonda_cuda")


def main() -> int:
    os.environ["CUDA_VISIBLE_DEVICES"] = entorno.UUID_5080  # antes de importar CTranslate2
    log.info("DLLs de CUDA desde: %s", [str(d) for d in entorno.preparar_cuda()])

    import ctranslate2
    from faster_whisper import WhisperModel

    n = ctranslate2.get_cuda_device_count()
    log.info("ctranslate2 %s · GPUs visibles: %d", ctranslate2.__version__, n)
    if n != 1:
        log.error("Se esperaba exactamente 1 GPU visible")
        return 3

    otras = entorno.vram_otras_mib()
    t = time.time()
    modelo = WhisperModel(str(entorno.carpeta_modelo("large-v3")), device="cuda", device_index=0,
                          compute_type="int8_float16")
    log.info("large-v3 cargado en %.1f s", time.time() - t)
    ruido = (np.random.default_rng(0).standard_normal(16000 * 2) * 0.01).astype("float32")
    t = time.time()
    segs, _ = modelo.transcribe(ruido, language="es", beam_size=1)
    list(segs)
    log.info("Transcripción de prueba (usa cuBLAS y cuDNN): %.2f s", time.time() - t)
    log.info("VRAM de las demás GPUs antes/después: %d / %d MiB", otras, entorno.vram_otras_mib())
    log.info("OK: CTranslate2 funciona en la GPU permitida sin PyTorch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
