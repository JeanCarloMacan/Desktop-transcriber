"""Graba tus audios de prueba para F0: frases tipo dictado (con su texto de referencia) y tomas libres.

Guarda en datos\\f0\\audios\\ un .wav de 16 kHz mono por toma y, para las frases, un .txt con la referencia.

Uso (desde la raíz del proyecto):
    .venv\\Scripts\\python spikes\\f0\\grabar.py --listar              # ver micrófonos
    .venv\\Scripts\\python spikes\\f0\\grabar.py                       # micrófono por defecto
    .venv\\Scripts\\python spikes\\f0\\grabar.py --dispositivo ZOOM --desde 4 --libres 2
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

import comun

log = logging.getLogger("grabar")

AQUI = Path(__file__).resolve().parent
DESTINO = AQUI.parents[1] / "datos" / "f0" / "audios"


def args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true", help="lista los dispositivos de entrada y sale")
    ap.add_argument("--dispositivo", default=None, help="índice o parte del nombre del micrófono")
    ap.add_argument("--desde", type=int, default=1, help="empieza en la frase N (para retomar)")
    ap.add_argument("--libres", type=int, default=2, help="tomas libres sin referencia al final")
    return ap.parse_args()


def elegir_dispositivo(texto: str | None) -> int | None:
    import sounddevice as sd

    if texto is None:
        return None
    if texto.isdigit():
        return int(texto)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and texto.lower() in d["name"].lower():
            return i
    raise SystemExit(f"No hay micrófono que contenga {texto!r}; usa --listar")


def grabar(dispositivo: int | None) -> tuple[np.ndarray, int]:
    """Graba hasta que se pulsa Enter. Devuelve audio mono float32 y su frecuencia de muestreo."""
    import sounddevice as sd

    info = sd.query_devices(dispositivo, "input")
    sr = int(info["default_samplerate"])
    bloques: list[np.ndarray] = []

    def recibir(indata: np.ndarray, frames: int, tiempo: object, estado: sd.CallbackFlags) -> None:
        if estado:
            log.warning("audio: %s", estado)
        bloques.append(indata.mean(axis=1).copy())

    with sd.InputStream(device=dispositivo, channels=min(2, int(info["max_input_channels"])), samplerate=sr,
                        dtype="float32", callback=recibir):
        input("   ● grabando… Enter para terminar ")
    return (np.concatenate(bloques) if bloques else np.zeros(0, np.float32)), sr


def guardar(nombre: str, audio: np.ndarray, sr: int, referencia: str | None) -> None:
    import soundfile as sf
    import soxr

    DESTINO.mkdir(parents=True, exist_ok=True)
    audio16 = soxr.resample(audio, sr, comun.SR) if sr != comun.SR else audio
    sf.write(str(DESTINO / f"{nombre}.wav"), audio16, comun.SR, subtype="PCM_16")
    if referencia:
        (DESTINO / f"{nombre}.txt").write_text(referencia + "\n", encoding="utf-8")


def tomar(nombre: str, indicacion: str, dispositivo: int | None, referencia: str | None) -> None:
    log.info("\n%s", indicacion)
    while True:
        if input("   Enter para empezar (s = saltar) ").strip().lower() == "s":
            return
        audio, sr = grabar(dispositivo)
        pico = float(np.abs(audio).max()) if audio.size else 0.0
        log.info("   %.1f s · pico %.2f%s", audio.size / sr, pico,
                 "  ← muy bajo, acércate o sube la ganancia" if pico < 0.05 else
                 "  ← satura, baja la ganancia" if pico > 0.98 else "")
        if input("   Enter = guardar · r = repetir ").strip().lower() != "r":
            guardar(nombre, audio, sr, referencia)
            log.info("   guardado %s", DESTINO / f"{nombre}.wav")
            return


def main() -> int:
    a = args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sounddevice as sd

    if a.listar:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                log.info("%3d  %s  (%d Hz)", i, d["name"], int(d["default_samplerate"]))
        return 0

    dispositivo = elegir_dispositivo(a.dispositivo)
    log.info("Micrófono: %s", sd.query_devices(dispositivo, "input")["name"])
    frases = [l.strip() for l in (AQUI / "frases_f0.txt").read_text(encoding="utf-8-sig").splitlines()
              if l.strip() and not l.lstrip().startswith("#")]
    for i, frase in enumerate(frases, 1):
        if i >= a.desde:
            tomar(f"dictado_{i:02d}", f"[{i}/{len(frases)}] Lee con tu ritmo de dictado:\n   «{frase}»",
                  dispositivo, frase)
    for j in range(1, a.libres + 1):
        tomar(f"libre_{j}", f"[libre {j}/{a.libres}] Habla ~30 s sobre lo que estés revisando, como si dictaras feedback.",
              dispositivo, None)
    log.info("\nListo. Ahora: .venv\\Scripts\\python spikes\\f0\\bench.py --set dictado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
