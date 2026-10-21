"""Fuentes de audio: micrófono (solo abierto durante una sesión) y archivo WAV (pruebas y demos).

Ambas entregan float32 mono a 16 kHz por un callback que se llama desde su propio hilo.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
Entrega = Callable[[np.ndarray], None]


class Fuente(Protocol):
    def iniciar(self) -> None: ...

    def detener(self) -> None: ...


def elegir_microfono(nombre: str) -> int | None:
    """Índice del primer dispositivo de entrada cuyo nombre contiene `nombre`; None = el de Windows por defecto."""
    if not nombre:
        return None
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and nombre.lower() in d["name"].lower():
            return i
    raise ValueError(f"No hay micrófono que contenga {nombre!r}")


class FuenteMicrofono:
    def __init__(self, entregar: Entrega, dispositivo: str = "", bloque_s: float = 0.05) -> None:
        self._entregar = entregar
        self._dispositivo = dispositivo
        self._bloque_s = bloque_s
        self._stream: Any = None
        self._remuestreo: Any = None

    def iniciar(self) -> None:
        import sounddevice as sd
        import soxr

        indice = elegir_microfono(self._dispositivo)
        info = sd.query_devices(indice, "input")
        sr = int(info["default_samplerate"])
        self._remuestreo = soxr.ResampleStream(sr, SR, 1, dtype="float32")
        self._stream = sd.InputStream(device=indice, channels=min(2, int(info["max_input_channels"])), samplerate=sr,
                                      dtype="float32", blocksize=int(sr * self._bloque_s), callback=self._recibir)
        self._stream.start()
        log.info("Micrófono abierto: %s (%d Hz)", info["name"], sr)

    def _recibir(self, indata: np.ndarray, frames: int, tiempo: Any, estado: Any) -> None:
        if estado:
            log.warning("Audio: %s", estado)
        salida = self._remuestreo.resample_chunk(indata.mean(axis=1))
        if salida.size:
            self._entregar(np.ascontiguousarray(salida, dtype=np.float32))

    def detener(self) -> None:
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        cola = self._remuestreo.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
        if cola.size:
            self._entregar(np.ascontiguousarray(cola, dtype=np.float32))
        log.info("Micrófono cerrado")


class FuenteArchivo:
    """Reproduce un WAV como si fuera el micrófono, a `velocidad` veces el tiempo real; luego entrega silencio."""

    def __init__(self, entregar: Entrega, ruta: Path, velocidad: float = 1.0, bloque_s: float = 0.1) -> None:
        self._entregar = entregar
        self._ruta = ruta
        self._velocidad = velocidad
        self._bloque = int(SR * bloque_s)
        self._parar = threading.Event()
        self.terminado = threading.Event()
        self._hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        import soundfile as sf
        import soxr

        datos, sr = sf.read(str(self._ruta), dtype="float32", always_2d=True)
        audio = datos.mean(axis=1)
        self._audio = soxr.resample(audio, sr, SR).astype(np.float32) if sr != SR else audio
        self._hilo = threading.Thread(target=self._reproducir, daemon=True, name="fuente-archivo")
        self._hilo.start()

    def _reproducir(self) -> None:
        pausa = self._bloque / SR / self._velocidad
        for i in range(0, len(self._audio), self._bloque):
            if self._parar.wait(pausa):
                return
            self._entregar(self._audio[i:i + self._bloque])
        self.terminado.set()
        silencio = np.zeros(self._bloque, dtype=np.float32)
        while not self._parar.wait(pausa):
            self._entregar(silencio)

    def detener(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2)
