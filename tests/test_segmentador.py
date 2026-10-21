"""El segmentador cierra frases en las pausas, emite parciales y descarta ruido y alucinaciones."""
from __future__ import annotations

import numpy as np

from voice_core.segmentador import SR, ParamsSegmentacion, Segmentador


def voz(s: float) -> np.ndarray:
    return np.full(int(SR * s), 0.1, dtype=np.float32)


def silencio(s: float) -> np.ndarray:
    return np.zeros(int(SR * s), dtype=np.float32)


def prob_por_energia(w: np.ndarray) -> float:
    return 1.0 if float(np.abs(w).mean()) > 0.01 else 0.0


class Transcriptor:
    def __init__(self, texto: str | None = None) -> None:
        self.texto = texto
        self.llamadas: list[tuple[float, bool]] = []

    def __call__(self, audio: np.ndarray, final: bool) -> str:
        self.llamadas.append((len(audio) / SR, final))
        return self.texto if self.texto is not None else f"{'F' if final else 'P'}{len(audio) / SR:.2f}"


def segmentador(t: Transcriptor, **kw: object) -> Segmentador:
    """Las pruebas fijan 700 ms de pausa: prueban la lógica, no el valor por defecto."""
    return Segmentador(ParamsSegmentacion(**{"silencio_fin_ms": 700, **kw}), prob_por_energia, t)  # type: ignore[arg-type]


def test_el_defecto_no_parte_frases_por_pausas_para_pensar() -> None:
    """Con el valor por defecto (1000 ms), una pausa de 0,8 s a mitad de frase no la cierra."""
    s = Segmentador(ParamsSegmentacion(parciales=False), prob_por_energia, Transcriptor())
    eventos = s.alimentar(np.concatenate([voz(1.0), silencio(0.8), voz(1.0), silencio(1.2)]))
    assert len(finales(eventos)) == 1


def finales(eventos: list[tuple[str, dict]]) -> list[dict]:
    return [d for tipo, d in eventos if tipo == "final"]


def test_segmentador_cierra_una_frase_por_pausa() -> None:
    """Dos tramos de voz separados por 1 s de silencio dan dos finales con tiempos coherentes."""
    s = segmentador(Transcriptor(), parciales=False)
    eventos = s.alimentar(np.concatenate([voz(1.0), silencio(1.0), voz(0.5), silencio(1.0)]))
    f = finales(eventos)
    assert len(f) == 2
    assert f[0]["t0"] < 0.05 and 1.0 < f[0]["t1"] < 1.3
    assert 1.6 < f[1]["t0"] < 2.05 and f[1]["t1"] < 2.8


def test_segmentador_no_cierra_con_pausas_cortas() -> None:
    """Una pausa de 0,4 s (menor que silencio_fin_ms) no parte la frase."""
    s = segmentador(Transcriptor(), parciales=False)
    eventos = s.alimentar(np.concatenate([voz(0.8), silencio(0.4), voz(0.8), silencio(1.0)]))
    assert len(finales(eventos)) == 1


def test_segmentador_emite_parciales_mientras_se_habla() -> None:
    t = Transcriptor()
    s = segmentador(t, parcial_cada_s=0.4)
    eventos = []
    for _ in range(10):  # 2 s de voz en trozos de 0,2 s
        eventos += s.alimentar(voz(0.2))
    parciales = [d for tipo, d in eventos if tipo == "parcial"]
    assert 3 <= len(parciales) <= 6
    assert not any(final for _, final in t.llamadas)


def test_segmentador_sin_parciales_en_cpu() -> None:
    t = Transcriptor()
    s = segmentador(t, parciales=False)
    for _ in range(10):
        s.alimentar(voz(0.2))
    assert t.llamadas == []


def test_segmentador_vaciar_cierra_la_frase_abierta() -> None:
    s = segmentador(Transcriptor(), parciales=False)
    assert s.alimentar(voz(1.0)) == []
    assert len(finales(s.vaciar())) == 1
    assert s.vaciar() == []


def test_segmentador_corta_frases_largas() -> None:
    s = segmentador(Transcriptor(), parciales=False, max_frase_s=3.0)
    f = finales(s.alimentar(np.concatenate([voz(7.0), silencio(1.0)])))
    assert len(f) == 3
    assert all(d["t1"] - d["t0"] <= 3.05 for d in f)


def test_segmentador_descarta_ruidos_cortos() -> None:
    """Un golpe de 0,1 s (menor que min_voz_ms) no llega al transcriptor."""
    t = Transcriptor()
    s = segmentador(t, parciales=False)
    assert finales(s.alimentar(np.concatenate([voz(0.1), silencio(1.0)]))) == []
    assert t.llamadas == []


def test_segmentador_filtra_alucinaciones_conocidas() -> None:
    for alucinacion in ("Subtítulos realizados por la comunidad de Amara.org",
                        "Subtítulos en español por José Miguel Pinto Escuela de Lingüística PUCE"):
        s = segmentador(Transcriptor(alucinacion))
        eventos = s.alimentar(np.concatenate([voz(1.5), silencio(1.0)]))
        assert eventos == [], alucinacion


def test_segmentador_no_filtra_la_palabra_subtitulos_en_un_dictado() -> None:
    s = segmentador(Transcriptor("Revisa los subtítulos del video por la mañana."), parciales=False)
    assert len(finales(s.alimentar(np.concatenate([voz(1.0), silencio(1.0)])))) == 1


def test_segmentador_no_da_parciales_con_muy_poco_audio() -> None:
    """El primer parcial espera a tener parcial_min_s de frase: con menos, Whisper inventa créditos."""
    t = Transcriptor()
    s = segmentador(t, parcial_cada_s=0.1, parcial_min_s=0.8, preroll_ms=0)
    eventos = []
    for _ in range(3):  # 0,6 s de voz
        eventos += s.alimentar(voz(0.2))
    assert eventos == [] and t.llamadas == []
    assert [tipo for tipo, _ in s.alimentar(voz(0.3))] == ["parcial"]


def test_segmentador_reiniciar_vuelve_el_tiempo_a_cero() -> None:
    s = segmentador(Transcriptor(), parciales=False)
    s.alimentar(np.concatenate([voz(1.0), silencio(1.0)]))
    s.reiniciar()
    f = finales(s.alimentar(np.concatenate([voz(1.0), silencio(1.0)])))
    assert f[0]["t0"] < 0.05


def test_segmentador_recorta_el_silencio_final() -> None:
    """La frase conserva como mucho ~200 ms de silencio al final, no los 700 ms que tardó en cerrarse."""
    t = Transcriptor()
    s = segmentador(t, parciales=False, preroll_ms=0)
    s.alimentar(np.concatenate([voz(1.0), silencio(1.5)]))
    duracion, final = t.llamadas[-1]
    assert final and duracion < 1.3
