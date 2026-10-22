"""El documento intercala frases e imágenes por tiempo y se adapta a chat, agente o texto."""
from __future__ import annotations

from pathlib import Path

from voice_core import documento

SEGMENTOS = [
    {"index": 0, "t0": 0.5, "t1": 4.0, "text": "Revisa la figura 3."},
    {"index": 1, "t0": 4.8, "t1": 9.0, "text": "La leyenda tapa la curva."},
    {"index": 2, "t0": 12.0, "t1": 15.0, "text": "Además, el eje X no coincide."},
]


def img(id_: int, t: float) -> dict:
    return {"id": id_, "t": t, "file": f"capturas/{id_:03d}.png"}


def test_la_imagen_va_tras_la_frase_que_se_estaba_diciendo() -> None:
    md = documento.generar("Dictado · hoy", SEGMENTOS, [img(1, 6.0)])  # durante la 2.ª frase
    assert md == ("# Dictado · hoy\n\nRevisa la figura 3. La leyenda tapa la curva.\n\n"
                  "![Imagen 1](capturas/001.png)\n\nAdemás, el eje X no coincide.\n")


def test_imagenes_entre_frases_al_principio_y_al_final() -> None:
    md = documento.generar("T", SEGMENTOS, [img(3, 20.0), img(1, 0.1), img(2, 10.0)])
    bloques = md.strip().split("\n\n")
    assert bloques == ["# T", "![Imagen 1](capturas/001.png)", "Revisa la figura 3. La leyenda tapa la curva.",
                       "![Imagen 2](capturas/002.png)", "Además, el eje X no coincide.", "![Imagen 3](capturas/003.png)"]


def test_sin_segmentos_quedan_solo_las_imagenes() -> None:
    assert documento.generar("T", [], [img(1, 2.0)]) == "# T\n\n![Imagen 1](capturas/001.png)\n"


def test_unir_lineas_reune_parrafos_partidos_sin_tocar_bloques() -> None:
    partido = ("# Título\n\nRevisa la figura 3: la leyenda tapa la curva del modelo y el eje vertical no\n"
               "tiene unidades.\n\n![Imagen 1](capturas/001.png)\n\n- uno\n- dos\n")
    assert documento.unir_lineas(partido) == ("# Título\n\nRevisa la figura 3: la leyenda tapa la curva del modelo y el "
                                              "eje vertical no tiene unidades.\n\n![Imagen 1](capturas/001.png)\n\n"
                                              "- uno\n- dos\n")


def test_para_chat_sin_titulo_y_con_marcadores() -> None:
    md = documento.generar("Dictado · hoy", SEGMENTOS, [img(1, 6.0)])
    assert documento.para_chat(md) == ("Revisa la figura 3. La leyenda tapa la curva.\n\n[Imagen 1]\n\n"
                                       "Además, el eje X no coincide.\n")
    assert documento.solo_texto(md) == "Revisa la figura 3. La leyenda tapa la curva.\n\nAdemás, el eje X no coincide.\n"


def test_para_agente_rutas_absolutas_aunque_haya_espacios(tmp_path: Path) -> None:
    carpeta = tmp_path / "una carpeta" / "s1"
    md = documento.con_rutas_absolutas(documento.generar("T", SEGMENTOS, [img(1, 6.0)]), carpeta)
    linea = next(l for l in md.split("\n") if l.startswith("!["))
    assert linea == f"![Imagen 1](<{(carpeta / 'capturas/001.png').as_posix()}>)"
    assert documento.imagenes(md) == [(carpeta / "capturas/001.png").as_posix()]


def test_numerar_imagenes_tras_editar() -> None:
    editado = "# T\n\n![captura](capturas/002.png)\n\ntexto\n\n![](capturas/001.png)\n"
    assert documento.numerar_imagenes(editado) == ("# T\n\n![Imagen 1](capturas/002.png)\n\ntexto\n\n"
                                                   "![Imagen 2](capturas/001.png)\n")
