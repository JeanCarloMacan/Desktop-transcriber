"""Segmentación en frases por VAD y emisión de texto parcial y final. Lógica pura, sin CUDA ni E/S.

Recibe audio a 16 kHz en trozos de cualquier tamaño, lo evalúa en ventanas de 32 ms (512 muestras) y:
- abre una frase cuando la probabilidad de voz supera `umbral` (con `preroll_ms` de audio previo);
- la cierra tras `silencio_fin_ms` por debajo de `umbral_bajo`, o al llegar a `max_frase_s`;
- al cerrarla pide la transcripción final; mientras está abierta, pide parciales cada `parcial_cada_s` de audio.
"""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

SR = 16000
VENTANA = 512
COLA_SILENCIO_MS = 200  # silencio que se conserva al final de cada frase

# Frases que Whisper inventa sobre ruido o silencio (créditos de subtítulos de su entrenamiento). Se buscan como
# créditos («Subtítulos [en español] [realizados] por …»), no la palabra suelta: decir «revisa los subtítulos» es válido.
ALUCINACIONES = re.compile(
    r"amara\.org|^\W*subt[ií]tulos( en español)?( realizados| hechos| creados)? por\b|gracias por ver el v[ií]deo"
    r"|suscr[ií]bete al canal", re.IGNORECASE)

Evento = tuple[str, dict]


@dataclass(frozen=True)
class ParamsSegmentacion:
    umbral: float = 0.5
    umbral_bajo: float = 0.35
    silencio_fin_ms: int = 1000  # con 700 ms las pausas para pensar partían frases (spikes/f1/contexto.py)
    max_frase_s: float = 28.0
    parcial_cada_s: float = 0.4
    parcial_min_s: float = 0.8  # con menos audio Whisper tiende a inventar créditos de subtítulos
    preroll_ms: int = 300
    min_voz_ms: int = 250
    parciales: bool = True


class Segmentador:
    def __init__(self, params: ParamsSegmentacion, prob_voz: Callable[[np.ndarray], float],
                 transcribir: Callable[[np.ndarray, bool], str]) -> None:
        self._p = params
        self._prob_voz = prob_voz
        self._transcribir = transcribir
        self.reiniciar()

    def reiniciar(self) -> None:
        """Nueva sesión: el tiempo vuelve a 0 y se olvida todo lo pendiente."""
        self._pendiente = np.zeros(0, dtype=np.float32)
        self._t = 0
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, self._p.preroll_ms * SR // 1000 // VENTANA))
        self._abrir_estado()
        reiniciar_vad = getattr(self._prob_voz, "reiniciar", None)
        if callable(reiniciar_vad):
            reiniciar_vad()

    def _abrir_estado(self) -> None:
        self._en_voz = False
        self._frase: list[np.ndarray] = []
        self._t0 = 0
        self._voz = 0
        self._silencio = 0
        self._ultimo_parcial = 0
        self._texto_parcial = ""

    def alimentar(self, audio: np.ndarray) -> list[Evento]:
        self._pendiente = np.concatenate([self._pendiente, np.asarray(audio, dtype=np.float32).reshape(-1)])
        eventos: list[Evento] = []
        n = len(self._pendiente) // VENTANA
        for i in range(n):
            eventos += self._ventana(self._pendiente[i * VENTANA:(i + 1) * VENTANA])
        self._pendiente = self._pendiente[n * VENTANA:]
        eventos += self._parcial()
        return eventos

    def vaciar(self) -> list[Evento]:
        """Fin de sesión: procesa lo pendiente y cierra la frase abierta."""
        eventos: list[Evento] = []
        if len(self._pendiente):
            relleno = np.zeros(VENTANA - len(self._pendiente), dtype=np.float32)
            eventos += self._ventana(np.concatenate([self._pendiente, relleno]))
            self._pendiente = np.zeros(0, dtype=np.float32)
        if self._en_voz:
            eventos += self._cerrar()
        return eventos

    def _ventana(self, w: np.ndarray) -> list[Evento]:
        p = self._prob_voz(w)
        self._t += VENTANA
        if not self._en_voz:
            self._preroll.append(w)
            if p >= self._p.umbral:
                self._en_voz = True
                self._frase = list(self._preroll)
                self._t0 = self._t - len(self._frase) * VENTANA
                self._voz = VENTANA
                self._silencio = 0
            return []
        self._frase.append(w)
        if p >= self._p.umbral:
            self._voz += VENTANA
            self._silencio = 0
        elif p < self._p.umbral_bajo:
            self._silencio += VENTANA
        largo = len(self._frase) * VENTANA
        if self._silencio >= self._p.silencio_fin_ms * SR // 1000 or largo >= self._p.max_frase_s * SR:
            return self._cerrar()
        return []

    def _parcial(self) -> list[Evento]:
        if not (self._en_voz and self._p.parciales):
            return []
        largo = len(self._frase) * VENTANA
        if largo < self._p.parcial_min_s * SR or largo - self._ultimo_parcial < self._p.parcial_cada_s * SR:
            return []
        self._ultimo_parcial = largo
        texto = self._transcribir(np.concatenate(self._frase), False)
        if not texto or texto == self._texto_parcial or ALUCINACIONES.search(texto):
            return []
        self._texto_parcial = texto
        return [("parcial", {"texto": texto})]

    def _cerrar(self) -> list[Evento]:
        sobrante = max(0, self._silencio - COLA_SILENCIO_MS * SR // 1000) // VENTANA
        frase = self._frase[: len(self._frase) - sobrante] if sobrante else self._frase
        t0, voz = self._t0, self._voz
        self._abrir_estado()
        self._preroll.clear()
        if voz < self._p.min_voz_ms * SR // 1000 or not frase:
            return []
        audio = np.concatenate(frase)
        texto = self._transcribir(audio, True).strip()
        if not texto or ALUCINACIONES.search(texto):
            return []
        return [("final", {"texto": texto, "t0": round(t0 / SR, 3), "t1": round((t0 + len(audio)) / SR, 3)})]
