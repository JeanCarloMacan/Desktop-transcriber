"""Experimento F2: ¿cuánto cuesta «dormir» el modelo en RAM en lugar de cerrar el worker?

Mide, con large-v3 int8_float16 en la 5080: carga en frío, unload_model(to_cpu=True) (VRAM que queda),
load_model() de vuelta (tiempo de despertar) y que la transcripción sigue funcionando.
Uso (desde la raíz):  .venv\\Scripts\\python spikes\\f2\\reposo_ram.py
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from voice_core.ajustes import cargar  # noqa: E402  (no importa CUDA)

UUID_5080 = cargar().gpu_uuid or ""
os.environ["CUDA_VISIBLE_DEVICES"] = UUID_5080 or "-1"  # antes de importar CUDA

import numpy as np  # noqa: E402
from voice_core.gpu import preparar_cuda  # noqa: E402


def vram() -> int:
    salida = subprocess.run(["nvidia-smi", f"--id={UUID_5080}", "--query-gpu=memory.used",
                             "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return int(salida.strip())


def ram_mib() -> int:
    import ctypes
    from ctypes import wintypes

    class Contadores(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                           "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    c = Contadores()
    c.cb = ctypes.sizeof(c)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize // 2**20


def main() -> None:
    aj = cargar()
    preparar_cuda(aj.dirs_dll_cuda)
    from faster_whisper import WhisperModel

    base = vram()
    audio = (np.random.default_rng(0).standard_normal(16000 * 3) * 0.003).astype(np.float32)
    t = time.perf_counter()
    m = WhisperModel(str(aj.ruta_modelo), device="cuda", compute_type="int8_float16")
    list(m.transcribe(audio, language="es")[0])
    print(f"carga en frío + calentamiento: {time.perf_counter() - t:.1f} s · VRAM +{vram() - base} MiB · RAM {ram_mib()} MiB")
    print("¿unload_model disponible?", hasattr(m.model, "unload_model"))
    for ronda in (1, 2):
        t = time.perf_counter()
        m.model.unload_model(to_cpu=True)
        dormir = time.perf_counter() - t
        time.sleep(1)
        print(f"[{ronda}] dormido en RAM en {dormir:.2f} s · VRAM +{vram() - base} MiB · RAM {ram_mib()} MiB")
        t = time.perf_counter()
        m.model.load_model()
        despertar = time.perf_counter() - t
        t2 = time.perf_counter()
        list(m.transcribe(audio, language="es")[0])
        print(f"[{ronda}] despierto en {despertar:.2f} s · 1.ª transcripción {time.perf_counter() - t2:.2f} s "
              f"· VRAM +{vram() - base} MiB")


if __name__ == "__main__":
    main()
