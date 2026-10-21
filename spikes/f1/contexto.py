"""Experimento F1: ¿cómo evitar frases partidas («una pausa muy... Grande. Innecesaria.»)?

Reprocesa el audio guardado de una sesión real con el segmentador y el modelo de producción, variando:
- silencio_fin_ms (pausa que cierra una frase) y
- contexto: pasar a Whisper, además del glosario, el final del texto ya transcrito en la sesión.

Uso (desde la raíz):  .venv\\Scripts\\python spikes\\f1\\contexto.py datos\\sesiones\\<id>\\audio.wav
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from voice_core.ajustes import cargar  # noqa: E402  (no importa CUDA)

os.environ["CUDA_VISIBLE_DEVICES"] = cargar().gpu_uuid or "-1"  # antes de importar CUDA

import numpy as np  # noqa: E402
from voice_core.audio import SR  # noqa: E402
from voice_core.gpu import preparar_cuda  # noqa: E402
from voice_core.segmentador import ParamsSegmentacion, Segmentador  # noqa: E402

CONTEXTO_CARACTERES = 200
VARIANTES = [("A · actual (700 ms, sin contexto)", 700, False), ("B · 700 ms + contexto", 700, True),
             ("C · 1000 ms, sin contexto", 1000, False), ("D · 1000 ms + contexto", 1000, True)]


def main() -> int:
    import soundfile as sf

    aj = cargar()
    preparar_cuda(aj.dirs_dll_cuda)
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_vad_model

    from voice_core.worker import NO_VOZ_LOGPROB, NO_VOZ_PROB, VadSilero

    audio, sr = sf.read(sys.argv[1], dtype="float32")
    assert sr == SR
    modelo = WhisperModel(str(aj.ruta_modelo), device="cuda", compute_type=aj.motor.compute_gpu)
    glosario = aj.texto_glosario or ""

    for nombre, silencio_ms, con_contexto in VARIANTES:
        finales: list[str] = []
        tiempos: list[float] = []

        def transcribir(trozo: np.ndarray, final: bool) -> str:
            prompt = glosario
            if con_contexto and finales:
                prompt = f"{glosario} {' '.join(finales)[-CONTEXTO_CARACTERES:]}"
            t = time.perf_counter()
            segs, _ = modelo.transcribe(trozo, language="es", beam_size=5 if final else 1, initial_prompt=prompt,
                                        condition_on_previous_text=False, vad_filter=False, without_timestamps=True)
            texto = " ".join(s.text.strip() for s in segs
                             if not (s.no_speech_prob > NO_VOZ_PROB and s.avg_logprob < NO_VOZ_LOGPROB)).strip()
            if final:
                tiempos.append(time.perf_counter() - t)
            return texto

        params = ParamsSegmentacion(**{**aj.segmentacion.model_dump(), "silencio_fin_ms": silencio_ms,
                                       "parciales": False})
        seg = Segmentador(params, VadSilero(get_vad_model().session), transcribir)
        for i in range(0, len(audio), 1600):
            for tipo, datos in seg.alimentar(audio[i:i + 1600]):
                finales.append(datos["texto"])
        finales += [d["texto"] for _, d in seg.vaciar()]

        texto = " ".join(finales)
        cortes = len(re.findall(r"\.\.\.|…", texto))
        mayus = sum(1 for prev, cur in zip(finales, finales[1:])
                    if not re.search(r"[.?!]$", prev.rstrip(".…") if prev.endswith(("...", "…")) else prev)
                    or prev.endswith(("...", "…")) and cur[:1].isupper())
        print(f"\n=== {nombre}: {len(finales)} frases · {cortes} «...» · {mayus} cortes con mayúscula a mitad de frase"
              f" · espera tras la pausa ≈ {silencio_ms / 1000 + np.median(tiempos):.2f} s")
        print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
