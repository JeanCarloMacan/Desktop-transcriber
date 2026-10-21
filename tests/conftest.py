"""Dobles de prueba: worker, GPU y fuente de audio falsos, para probar sin GPU ni micrófono."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voice_core.ajustes import Ajustes, AjustesDatos, AjustesMotor


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def aj(tmp_path: Path) -> Ajustes:
    return Ajustes(raiz=tmp_path, datos=AjustesDatos(carpeta=tmp_path / "datos"),
                   motor=AjustesMotor(glosario=None, inactividad_min=1, cuda_dll_dirs=[tmp_path], gpu="GPU-prueba"))


class GpuFalsa:
    def __init__(self, libre: int = 12000, usada: int = 1200) -> None:
        self.libre, self.usada = libre, usada

    def memoria(self, uuid: str) -> tuple[int, int] | None:
        return self.usada, self.libre


class WorkerFalso:
    """Imita el protocolo del stt-worker: un parcial por trozo de audio y una frase final al vaciar."""

    def __init__(self, cfg: dict[str, Any], al_mensaje: Callable[[str, dict], None], auto_listo: bool) -> None:
        self.cfg = cfg
        self._al = al_mensaje
        self.vivo = True
        self.cerrado = False
        self.recibidos: list[str] = []
        self.muestras = 0
        if auto_listo:
            self.listo()

    def listo(self) -> None:
        self._al("listo", {"dispositivo": self.cfg["dispositivo"], "carga_s": 0.1})

    def morir(self, codigo: int = 1) -> None:
        self.vivo = False
        self._al("murio", {"codigo": codigo})

    def enviar(self, tipo: str, datos: Any = None) -> None:
        self.recibidos.append(tipo)
        if tipo == "audio":
            self.muestras += len(datos)
            if self.cfg["segmentacion"]["parciales"]:
                self._al("parcial", {"texto": f"parcial {self.muestras}"})
        elif tipo == "reiniciar":
            self.muestras = 0
        elif tipo == "vaciar":
            if self.muestras:
                self._al("final", {"texto": f"frase de {self.muestras} muestras", "t0": 0.0,
                                   "t1": self.muestras / 16000})
            self._al("vaciado", {})
        elif tipo == "dormir":
            self._al("dormido", {"s": 0.6})
        elif tipo == "despertar":
            self._al("listo", {"dispositivo": self.cfg["dispositivo"], "carga_s": 0.35, "despertado": True})

    def cerrar(self, espera_s: float = 5.0) -> None:
        self.vivo = False
        self.cerrado = True


class FabricaFalsa:
    def __init__(self, auto_listo: bool = False) -> None:
        self.auto_listo = auto_listo
        self.creados: list[WorkerFalso] = []

    def __call__(self, cfg: dict[str, Any], al_mensaje: Callable[[str, dict], None]) -> WorkerFalso:
        w = WorkerFalso(cfg, al_mensaje, self.auto_listo)
        self.creados.append(w)
        return w

    @property
    def ultimo(self) -> WorkerFalso:
        return self.creados[-1]


class FuenteFalsa:
    def __init__(self, entregar: Callable[[np.ndarray], None]) -> None:
        self.entregar = entregar
        self.abierta = False

    def iniciar(self) -> None:
        self.abierta = True

    def detener(self) -> None:
        self.abierta = False


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def esperar(condicion: Callable[[], bool], limite_s: float = 3.0) -> None:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        if condicion():
            return
        time.sleep(0.01)
    raise AssertionError("La condición no se cumplió a tiempo")
