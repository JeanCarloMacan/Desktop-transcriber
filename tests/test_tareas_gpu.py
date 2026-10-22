"""Salvaguarda de la GPU de LM Studio: cuándo cuenta como ocupada y cómo se lee (con un lector de NVML falso)."""
from __future__ import annotations

from tasks_service.gpu import VigiaGpu, motivo_ocupada, nombre_corto
from voice_core.gpu import LecturaGpu


def lec(usada_mib: int, uso_pct: int) -> LecturaGpu:
    return LecturaGpu("NVIDIA GeForce RTX 5090", usada_mib, uso_pct)


class LectorFalso:
    def __init__(self, lecturas: list[LecturaGpu | None]) -> None:
        self.lecturas = lecturas
        self.veces = 0

    def lectura(self, uuid: str) -> LecturaGpu | None:
        self.veces += 1
        return self.lecturas.pop(0)


def test_libre_con_poca_memoria_y_sin_computo() -> None:
    assert motivo_ocupada([lec(10, 0), lec(12, 3), lec(10, 0)], 1024, 10) is None
    assert motivo_ocupada([], 1024, 10) is None


def test_ocupada_por_memoria_por_computo_sostenido_o_por_ambos() -> None:
    assert motivo_ocupada([lec(3277, 0)] * 3, 1024, 10) == "la RTX 5090 está ocupada (3,2 GB en uso)"
    assert motivo_ocupada([lec(500, 40), lec(500, 60), lec(500, 50)], 1024, 10) == \
        "la RTX 5090 está ocupada (50 % de cómputo)"
    assert motivo_ocupada([lec(12288, 95)] * 3, 1024, 10) == \
        "la RTX 5090 está ocupada (12,0 GB en uso y 95 % de cómputo)"


def test_un_pico_suelto_de_computo_no_bloquea_pero_la_memoria_si() -> None:
    assert motivo_ocupada([lec(10, 25), lec(10, 0), lec(10, 0)], 1024, 10) is None  # media 8 %
    assert motivo_ocupada([lec(10, 0), lec(2048, 0), lec(10, 0)], 1024, 10) == \
        "la RTX 5090 está ocupada (2,0 GB en uso)"


def test_nombre_corto() -> None:
    assert nombre_corto("NVIDIA GeForce RTX 5090") == "RTX 5090"
    assert nombre_corto("NVIDIA RTX A6000") == "RTX A6000"


def test_el_vigia_hace_varias_lecturas_y_si_nvml_falla_no_bloquea() -> None:
    lector = LectorFalso([lec(10, 0), lec(2048, 0), lec(10, 0)])
    assert VigiaGpu("GPU-x", 1024, 10, lector=lector, pausa_s=0).motivo() == "la RTX 5090 está ocupada (2,0 GB en uso)"
    assert lector.veces == 3
    assert VigiaGpu("GPU-x", 1024, 10, lector=LectorFalso([None]), pausa_s=0).motivo() is None
    assert VigiaGpu("GPU-x", 1024, 10, lector=LectorFalso([lec(10, 0)])).nombre() == "RTX 5090"
