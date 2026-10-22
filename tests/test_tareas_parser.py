"""F4: prompt, esquema y lectura de la respuesta del modelo (sin LM Studio)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tasks_service.parser import (OTROS, RespuestaInvalida, calendario, categorias_efectivas, esquema, interpretar,
                                  mensajes)
from voice_core.ajustes import cargar

CATS = ["Trabajo", "Personal", "Otros"]


def respuesta(*tareas: dict) -> str:
    return json.dumps({"tasks": list(tareas)}, ensure_ascii=False)


def tarea(title: str, category: str = "Trabajo", due: str | None = None, **extra: object) -> dict:
    return {"title": title, "category": category, "due": due, "notes": None, "quote": title, **extra}


def test_otros_se_anade_una_sola_vez() -> None:
    assert list(categorias_efectivas({"Trabajo": "a", "Personal": "b"})) == ["Trabajo", "Personal", OTROS]
    assert list(categorias_efectivas({"otros": "lo demás", "Trabajo": "a"})) == ["otros", "Trabajo"]


def test_el_esquema_cierra_categorias_y_formato_de_fecha() -> None:
    e = esquema(CATS)["properties"]["tasks"]["items"]
    assert e["properties"]["category"]["enum"] == CATS
    assert e["properties"]["due"]["anyOf"][0]["pattern"] == "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    assert set(e["required"]) == {"title", "category", "due", "notes", "quote"}
    assert e["additionalProperties"] is False


def test_el_prompt_lleva_fecha_calendario_categorias_y_dictado() -> None:
    sistema, usuario = mensajes("  Mandar el informe el viernes.  ", {"Trabajo": "proyectos"}, date(2026, 10, 21))
    assert sistema["role"] == "system" and "tareas pendientes" in sistema["content"]
    texto = usuario["content"]
    assert "Fecha del dictado: miércoles 21 de octubre de 2026 (2026-10-21)" in texto
    assert "- viernes 23 de octubre de 2026: 2026-10-23" in texto
    assert "- Trabajo: proyectos" in texto
    assert '"""\nMandar el informe el viernes.\n"""' in texto


def test_el_calendario_cruza_meses_con_los_dias_bien_nombrados() -> None:
    lineas = calendario(date(2026, 10, 30), dias=3).splitlines()
    assert lineas == ["- sábado 31 de octubre de 2026: 2026-10-31", "- domingo 1 de noviembre de 2026: 2026-11-01",
                      "- lunes 2 de noviembre de 2026: 2026-11-02"]


def test_interpretar_limpia_valida_y_quita_repetidas() -> None:
    propuestas = interpretar(respuesta(
        tarea("  Enviar   el informe ", due="2026-10-23"),
        tarea("enviar el informe."),  # la misma, con otra puntuación
        tarea("Comprar pilas", category="personal"),  # categoría con otra capitalización
        tarea("Algo raro", category="Inventada", due="2026-02-30"),  # categoría y fecha imposibles
        tarea("   "),
        tarea("revisar el presupuesto del proyecto"),  # copiado del dictado, en minúscula
    ), CATS)
    assert [(p.title, p.category, p.due) for p in propuestas] == [
        ("Enviar el informe", "Trabajo", "2026-10-23"), ("Comprar pilas", "Personal", None),
        ("Algo raro", "Otros", None), ("Revisar el presupuesto del proyecto", "Trabajo", None)]


def test_interpretar_acepta_json_envuelto_y_rechaza_lo_que_no_lo_es() -> None:
    envuelto = "```json\n" + respuesta(tarea("Subir el código")) + "\n```"
    assert interpretar(envuelto, CATS)[0].title == "Subir el código"
    assert interpretar('{"tasks": []}', CATS) == []
    with pytest.raises(RespuestaInvalida):
        interpretar("No hay tareas.", CATS)
    with pytest.raises(RespuestaInvalida):
        interpretar('{"tareas": []}', CATS)


def test_categorias_desde_el_toml_en_su_orden(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    ruta = tmp_path / "config" / "voice-core.toml"
    ruta.write_text('[tareas]\nmodelo = "qwen/qwen3.5-35b-a3b"\n\n[tareas.categorias]\n'
                    '"Investigación" = "papers"\nDocencia = "clases y exámenes"\n', encoding="utf-8")
    aj = cargar(ruta)
    assert aj.tareas.modelo == "qwen/qwen3.5-35b-a3b"
    assert list(categorias_efectivas(aj.tareas.categorias)) == ["Investigación", "Docencia", OTROS]
    assert aj.url_tareas == "http://127.0.0.1:8766"
