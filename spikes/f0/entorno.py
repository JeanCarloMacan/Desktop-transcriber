"""Recursos compartidos del spike F0: GPU fijada por UUID, DLLs de CUDA, VRAM por NVML y carpeta de modelos.

Regla: este módulo no importa ctranslate2 ni faster_whisper al cargarse. Quien vaya a usar CUDA debe llamar a
`preparar_cuda()` antes de importarlos, con `CUDA_VISIBLE_DEVICES` ya fijado en el entorno del proceso.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _uuid_permitido() -> str:
    """GPU del motor: TRT_GPU_UUID, o la que coincida con TRT_GPU (parte del nombre, por defecto «5080»)."""
    from voice_core.gpu import resolver_gpu

    uuid = os.environ.get("TRT_GPU_UUID") or resolver_gpu(os.environ.get("TRT_GPU", "5080"))
    if not uuid:
        raise SystemExit("Ninguna GPU coincide: define TRT_GPU (parte del nombre) o TRT_GPU_UUID")
    return uuid


# La única GPU que puede ver el motor. El resto (p. ej. la de los LLM) solo se vigila para comprobar que no cambia.
UUID_5080 = _uuid_permitido()

# Carpetas normales <org>--<repo>, sin symlinks (la caché de Hugging Face falla en Windows sin modo desarrollador).
MODELOS_DIR = Path(os.environ.get("TRT_MODELOS_DIR", Path.home() / ".cache" / "transcriptor-rt" / "modelos"))

REPOS = {
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "small": "Systran/faster-whisper-small",
}
ARCHIVOS_MODELO = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def dirs_dll_cuda() -> list[Path]:
    """Carpetas con cuBLAS: TRT_CUDA_DLL_DIRS (separadas por ';') o, por defecto, el CUDA Toolkit instalado."""
    crudo = os.environ.get("TRT_CUDA_DLL_DIRS")
    if crudo:
        return [Path(p) for p in crudo.split(os.pathsep) if p]
    cuda_path = os.environ.get("CUDA_PATH")
    return [Path(cuda_path) / "bin"] if cuda_path else []


def preparar_cuda() -> list[Path]:
    """Añade las carpetas de DLLs de CUDA a la búsqueda de Windows. Llamar antes de importar ctranslate2."""
    añadidas = []
    for d in dirs_dll_cuda():
        if d.is_dir():
            os.add_dll_directory(str(d))
            añadidas.append(d)
        else:
            log.warning("Carpeta de DLLs de CUDA inexistente: %s", d)
    return añadidas


def vram_usada_mib(uuid: str = UUID_5080) -> int:
    """VRAM usada en la GPU indicada, leída por NVML (no crea contexto CUDA)."""
    import pynvml

    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByUUID(uuid)
        return int(pynvml.nvmlDeviceGetMemoryInfo(h).used // (1024 * 1024))
    finally:
        pynvml.nvmlShutdown()


def vram_otras_mib() -> int:
    """VRAM usada en todas las demás GPUs (deben quedar igual mientras corre el motor)."""
    from voice_core.gpu import listar_gpus

    return sum(vram_usada_mib(u) for u, _ in listar_gpus() if u != UUID_5080)


def vram_libre_mib(uuid: str = UUID_5080) -> int:
    import pynvml

    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByUUID(uuid)
        return int(pynvml.nvmlDeviceGetMemoryInfo(h).free // (1024 * 1024))
    finally:
        pynvml.nvmlShutdown()


def carpeta_modelo(nombre: str) -> Path:
    """Carpeta local de un modelo por nombre corto (large-v3-turbo) o repo (org/nombre)."""
    repo = REPOS.get(nombre, nombre)
    return MODELOS_DIR / repo.replace("/", "--")


def modelo_presente(nombre: str) -> bool:
    d = carpeta_modelo(nombre)
    return (d / "model.bin").is_file() and (d / "config.json").is_file()


def descargar_modelo(nombre: str) -> Path:
    """Descarga el modelo a su carpeta compartida (sin symlinks). No importa faster_whisper ni CUDA."""
    from huggingface_hub import snapshot_download

    destino = carpeta_modelo(nombre)
    if modelo_presente(nombre):
        log.info("Modelo ya presente, no se descarga: %s", destino)
        return destino
    destino.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    repo = REPOS.get(nombre, nombre)
    log.info("Descargando %s → %s", repo, destino)
    snapshot_download(repo, local_dir=str(destino), allow_patterns=ARCHIVOS_MODELO)
    return destino
