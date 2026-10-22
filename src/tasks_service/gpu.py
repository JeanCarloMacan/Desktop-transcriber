"""Salvaguarda de la GPU de LM Studio: antes de pedir la carga del modelo se mira por NVML (solo lee contadores, no
crea contexto CUDA) si otro programa la está usando. `motivo_ocupada` es lógica pura; `VigiaGpu` hace las lecturas."""
from __future__ import annotations

import logging
import time
from typing import Protocol

from voice_core.gpu import LecturaGpu, Nvml

log = logging.getLogger(__name__)


def nombre_corto(nombre: str) -> str:
    """«NVIDIA GeForce RTX 5090» → «RTX 5090»."""
    for prefijo in ("NVIDIA ", "GeForce "):
        nombre = nombre.removeprefix(prefijo)
    return nombre


def motivo_ocupada(lecturas: list[LecturaGpu], vram_max_mib: int, uso_max_pct: int) -> str | None:
    """None si la GPU está libre; si no, el motivo para el aviso. VRAM: la lectura más alta; cómputo: la media, para
    que un pico suelto de otro programa no bloquee y un uso sostenido sí."""
    if not lecturas:
        return None
    usada = max(x.usada_mib for x in lecturas)
    uso = round(sum(x.uso_pct for x in lecturas) / len(lecturas))
    partes = []
    if usada > vram_max_mib:
        partes.append(f"{usada / 1024:.1f} GB en uso".replace(".", ","))
    if uso > uso_max_pct:
        partes.append(f"{uso} % de cómputo")
    if not partes:
        return None
    return f"la {nombre_corto(lecturas[0].nombre)} está ocupada ({' y '.join(partes)})"


class LectorGpu(Protocol):
    def lectura(self, uuid: str) -> LecturaGpu | None: ...


class VigiaGpu:
    def __init__(self, uuid: str, vram_max_mib: int, uso_max_pct: int, lector: LectorGpu | None = None,
                 muestras: int = 3, pausa_s: float = 0.5) -> None:
        self._uuid = uuid
        self.vram_max_mib = vram_max_mib
        self.uso_max_pct = uso_max_pct
        self._lector = lector or Nvml()
        self._muestras = muestras
        self._pausa_s = pausa_s

    def nombre(self) -> str | None:
        lectura = self._lector.lectura(self._uuid)
        return nombre_corto(lectura.nombre) if lectura else None

    def motivo(self) -> str | None:
        """Bloqueante (≈ 1 s): llamarlo desde un hilo. Si NVML no puede leer la GPU, se avisa en el registro y se da
        por libre: es preferible extraer a dejar las tareas esperando para siempre."""
        lecturas = []
        for i in range(self._muestras):
            if i:
                time.sleep(self._pausa_s)
            lectura = self._lector.lectura(self._uuid)
            if lectura is None:
                log.warning("No se pudo leer la GPU de LM Studio: se extrae sin comprobarla")
                return None
            lecturas.append(lectura)
        return motivo_ocupada(lecturas, self.vram_max_mib, self.uso_max_pct)
