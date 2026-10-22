"""Prompt, JSON Schema y lectura de la respuesta del LLM que extrae tareas de un dictado. Lógica pura, sin E/S."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

OTROS = "Otros"
DESCRIPCION_OTROS = "lo que no encaja en ninguna de las demás"
MAX_TITULO = 120
DIAS_CALENDARIO = 21
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SISTEMA = """Eres el organizador de tareas de una persona. Recibes la transcripción automática de algo que dictó en \
voz alta, en español: puede traer muletillas, repeticiones, autocorrecciones («el lunes, no, mejor el martes») y algún \
error de transcripción.

Extrae solo las tareas pendientes: acciones que la persona se propone hacer o encargar a alguien. No son tareas las \
opiniones, los comentarios, las descripciones ni lo que ya está hecho.

Para cada tarea:
- title: frase breve en infinitivo («Enviar el informe a Marta»), en español y sin muletillas; conserva tal cual los \
nombres propios y los términos técnicos.
- category: la categoría de la lista que mejor encaje; si ninguna encaja, «Otros».
- due: la fecha límite en formato AAAA-MM-DD, solo si el dictado indica un día o un plazo; resuelve las fechas \
relativas («mañana», «el viernes», «fin de mes», «el jueves de la semana que viene») con el calendario que se te da; \
si no hay plazo, null.
- notes: un detalle útil que no quepa en el título (cómo, para qué, con quién), o null.
- quote: el fragmento literal del dictado del que sale la tarea.

Cada acción distinta es una tarea aparte, aunque se digan en la misma frase. Si la misma acción se menciona varias \
veces, es una sola tarea; si hay una autocorrección, vale la última versión. Si no hay ninguna tarea, devuelve una \
lista vacía."""


@dataclass(frozen=True)
class Propuesta:
    title: str
    category: str
    due: str | None
    notes: str | None
    quote: str | None


class RespuestaInvalida(ValueError):
    """El LLM no devolvió el JSON esperado."""


def categorias_efectivas(configuradas: dict[str, str]) -> dict[str, str]:
    """Las categorías configuradas (en su orden) más «Otros» al final si no está."""
    resultado = {n.strip(): d.strip() for n, d in configuradas.items() if n.strip()}
    if not any(_clave(n) == _clave(OTROS) for n in resultado):
        resultado[OTROS] = DESCRIPCION_OTROS
    return resultado


def esquema(categorias: list[str]) -> dict:
    """JSON Schema que el servidor impone al generar: categoría cerrada y fecha AAAA-MM-DD o null."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["tasks"],
        "properties": {"tasks": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "category", "due", "notes", "quote"],
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string", "enum": categorias},
                "due": {"anyOf": [{"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}, {"type": "null"}]},
                "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "quote": {"type": "string"},
            },
        }}},
    }


def fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def calendario(desde: date, dias: int = DIAS_CALENDARIO) -> str:
    """Los próximos días con su nombre: los modelos se equivocan al contar días de la semana de cabeza."""
    return "\n".join(f"- {fecha_larga(d)}: {d.isoformat()}"
                     for d in (desde + timedelta(days=i) for i in range(1, dias + 1)))


def mensajes(texto: str, categorias: dict[str, str], fecha: date) -> list[dict[str, str]]:
    lista = "\n".join(f"- {nombre}: {descripcion}" for nombre, descripcion in categorias.items())
    usuario = (f"Fecha del dictado: {fecha_larga(fecha)} ({fecha.isoformat()}).\n\n"
               f"Calendario de los días siguientes:\n{calendario(fecha)}\n\n"
               f"Categorías:\n{lista}\n\n"
               f"Dictado:\n\"\"\"\n{texto.strip()}\n\"\"\"")
    return [{"role": "system", "content": SISTEMA}, {"role": "user", "content": usuario}]


def interpretar(contenido: str, categorias: list[str]) -> list[Propuesta]:
    """Valida y limpia la respuesta: títulos sin repetir, categoría conocida (o «Otros») y fechas reales."""
    datos = _json(contenido)
    tareas = datos.get("tasks") if isinstance(datos, dict) else None
    if not isinstance(tareas, list):
        raise RespuestaInvalida("Falta la lista «tasks» en la respuesta del modelo")
    por_clave = {_clave(c): c for c in categorias}
    vistas: set[str] = set()
    resultado: list[Propuesta] = []
    for t in tareas:
        if not isinstance(t, dict):
            continue
        titulo = _limpio(t.get("title"))
        if not titulo or _clave(titulo) in vistas:
            continue
        titulo = titulo[0].upper() + titulo[1:]  # a veces copia el dictado tal cual: «revisar el presupuesto…»
        vistas.add(_clave(titulo))
        categoria = por_clave.get(_clave(_limpio(t.get("category")) or ""), OTROS)
        resultado.append(Propuesta(title=titulo[:MAX_TITULO].rstrip(), category=categoria, due=_fecha(t.get("due")),
                                   notes=_limpio(t.get("notes")), quote=_limpio(t.get("quote"))))
    return resultado


def _json(contenido: str) -> object:
    texto = contenido.strip()
    inicio, fin = texto.find("{"), texto.rfind("}")  # por si llega envuelto en ```json … ``` o con texto alrededor
    if inicio < 0 or fin < inicio:
        raise RespuestaInvalida("La respuesta del modelo no contiene JSON")
    try:
        return json.loads(texto[inicio:fin + 1])
    except json.JSONDecodeError as e:
        raise RespuestaInvalida(f"JSON inválido en la respuesta del modelo: {e}") from e


def _limpio(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = " ".join(valor.split())
    return texto or None


def _fecha(valor: object) -> str | None:
    if not isinstance(valor, str) or not _FECHA.match(valor.strip()):
        return None
    try:
        return date.fromisoformat(valor.strip()).isoformat()
    except ValueError:
        return None


def _clave(texto: str) -> str:
    """Para comparar sin mayúsculas, tildes ni puntuación final."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().strip(" .;:").split())
