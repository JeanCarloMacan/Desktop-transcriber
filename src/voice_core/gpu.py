"""Lectura de VRAM por NVML (no crea contexto CUDA) y DLLs de CUDA. Nada de aquí importa CTranslate2."""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def listar_gpus() -> list[tuple[str, str]]:
    """(UUID, nombre) de cada GPU NVIDIA según NVML; lista vacía si no hay NVML."""
    try:
        import pynvml
    except ImportError:
        return []
    try:
        pynvml.nvmlInit()
        try:
            gpus = []
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                gpus.append((str(pynvml.nvmlDeviceGetUUID(h)), str(pynvml.nvmlDeviceGetName(h))))
            return gpus
        finally:
            pynvml.nvmlShutdown()
    except pynvml.NVMLError as e:
        log.warning("NVML no pudo listar las GPUs: %s", e)
        return []


def resolver_gpu(texto: str, gpus: list[tuple[str, str]] | None = None) -> str | None:
    """UUID de la única GPU permitida. `texto` es un UUID («GPU-…», se usa tal cual) o parte del nombre («5080»).

    Si el nombre no coincide con ninguna GPU, o coincide con varias, devuelve None: es preferible usar la CPU a
    arriesgarse a cargar el modelo en una GPU que no tocaba.
    """
    texto = texto.strip()
    if not texto:
        return None
    if texto.upper().startswith("GPU-"):
        return texto
    candidatas = [(uuid, nombre) for uuid, nombre in (gpus if gpus is not None else listar_gpus())
                  if texto.lower() in nombre.lower()]
    if len(candidatas) == 1:
        log.info("GPU permitida: %s", candidatas[0][1])
        return candidatas[0][0]
    if candidatas:
        log.warning("«%s» coincide con varias GPUs (%s): no se usará ninguna", texto,
                    ", ".join(n for _, n in candidatas))
    else:
        log.warning("Ninguna GPU coincide con «%s»: el motor usará la CPU", texto)
    return None


class Nvml:
    """VRAM (usada, libre) en MiB de la GPU con ese UUID; None si NVML no está disponible."""

    def memoria(self, uuid: str) -> tuple[int, int] | None:
        try:
            import pynvml
        except ImportError:
            return None
        try:
            pynvml.nvmlInit()
            try:
                info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByUUID(uuid))
                return int(info.used // 2**20), int(info.free // 2**20)
            finally:
                pynvml.nvmlShutdown()
        except pynvml.NVMLError as e:
            log.warning("NVML no pudo leer %s: %s", uuid, e)
            return None


def preparar_cuda(dirs: list[Path]) -> list[Path]:
    """Añade carpetas con cuBLAS a la búsqueda de DLLs. Llamar antes de importar ctranslate2."""
    añadidas = []
    for d in dirs:
        if d.is_dir():
            os.add_dll_directory(str(d))
            añadidas.append(d)
        else:
            log.warning("Carpeta de DLLs de CUDA inexistente: %s", d)
    return añadidas
