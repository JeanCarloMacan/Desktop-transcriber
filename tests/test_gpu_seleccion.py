"""La GPU permitida se elige por nombre o UUID; ante la duda, ninguna (el motor usará la CPU)."""
from __future__ import annotations

from voice_core.gpu import resolver_gpu

GPUS = [("GPU-aaaa", "NVIDIA GeForce RTX 5090"), ("GPU-bbbb", "NVIDIA GeForce RTX 5080")]


def test_por_parte_del_nombre() -> None:
    assert resolver_gpu("5080", GPUS) == "GPU-bbbb"
    assert resolver_gpu("rtx 5090", GPUS) == "GPU-aaaa"


def test_un_uuid_se_usa_tal_cual() -> None:
    assert resolver_gpu("GPU-cccc", GPUS) == "GPU-cccc"


def test_nombre_ambiguo_o_inexistente_no_elige_ninguna() -> None:
    assert resolver_gpu("RTX", GPUS) is None  # coincide con las dos: no se arriesga
    assert resolver_gpu("4090", GPUS) is None
    assert resolver_gpu("", GPUS) is None
    assert resolver_gpu("5080", []) is None  # sin NVIDIA / sin NVML
