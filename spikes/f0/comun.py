"""Audio y métricas del spike F0 (sin dependencias de CUDA)."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

SR = 16000


@dataclass(frozen=True)
class Clip:
    id: str
    wav: str
    referencia: str | None


def cargar_audio_16k(ruta: Path) -> np.ndarray:
    """WAV/FLAC → float32 mono a 16 kHz (soxr para remuestrear)."""
    import soundfile as sf
    import soxr

    datos, sr = sf.read(str(ruta), dtype="float32", always_2d=True)
    mono = datos.mean(axis=1)
    if sr != SR:
        mono = soxr.resample(mono, sr, SR)
    return np.ascontiguousarray(mono, dtype=np.float32)


def clips_de_carpeta(carpeta: Path, nombres: list[str] | None = None) -> list[Clip]:
    """Cada .wav de la carpeta (o solo los `nombres` dados, sin extensión); la referencia es el .txt hermano si existe."""
    wavs = [carpeta / f"{n}.wav" for n in nombres] if nombres else sorted(carpeta.glob("*.wav"))
    clips = []
    for wav in wavs:
        if not wav.is_file():
            continue
        txt = wav.with_suffix(".txt")
        ref = txt.read_text(encoding="utf-8-sig").strip() if txt.is_file() else None
        clips.append(Clip(id=wav.stem, wav=str(wav), referencia=ref))
    return clips


def guardar_clips(clips: list[Clip], ruta: Path) -> None:
    ruta.write_text(json.dumps([asdict(c) for c in clips], ensure_ascii=False, indent=2), encoding="utf-8")


def leer_clips(ruta: Path) -> list[Clip]:
    return [Clip(**c) for c in json.loads(ruta.read_text(encoding="utf-8"))]


def normalizar(texto: str) -> list[str]:
    """Minúsculas, cifras a palabras en español, sin puntuación. Conserva tildes: un acento mal puesto cuenta como error."""
    from num2words import num2words

    t = texto.lower()
    t = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", t)  # 3.000 → 3000
    t = re.sub(r"\d+", lambda m: f" {num2words(int(m.group()), lang='es')} ", t)
    t = t.replace("%", " por ciento ")
    t = re.sub(r"(?<=\w)-(?=\w)", "", t)  # fine-tuning = finetuning: solo cambia la grafía
    t = re.sub(r"[^\w\s]|_", " ", t)
    return t.split()


def distancia(a: list[str] | str, b: list[str] | str) -> int:
    """Distancia de Levenshtein entre dos secuencias."""
    previa = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        actual = [i]
        for j, y in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (x != y)))
        previa = actual
    return previa[-1]


def errores_palabras(ref: str, hip: str) -> tuple[int, int]:
    """(errores, palabras de la referencia) para acumular un WER global."""
    r, h = normalizar(ref), normalizar(hip)
    return distancia(r, h), len(r)


def errores_caracteres(ref: str, hip: str) -> tuple[int, int]:
    r, h = " ".join(normalizar(ref)), " ".join(normalizar(hip))
    return distancia(r, h), len(r)
