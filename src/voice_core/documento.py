"""Documento de una sesión en Markdown: generarlo, normalizarlo y adaptarlo para un chat o para un agente.

Lógica pura (sin E/S ni base de datos). Las imágenes se escriben como líneas `![Imagen N](capturas/00N.png)` con rutas
relativas a la carpeta de la sesión.
"""
from __future__ import annotations

import re
from pathlib import Path

IMAGEN = re.compile(r"^!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)\)\s*$")
INICIO_DE_BLOQUE = re.compile(r"^(#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s|```|\||!\[)")


def generar(titulo: str, segmentos: list[dict], adjuntos: list[dict]) -> str:
    """Párrafos de frases e imágenes ordenados por tiempo.

    Cada imagen va después de la frase que se estaba diciendo cuando se tomó (el último segmento con t0 ≤ t);
    las tomadas antes de la primera frase van al principio. Las frases seguidas forman un párrafo.
    """
    imagenes = sorted(adjuntos, key=lambda a: (a["t"], a["id"]))
    bloques: list[str] = []
    parrafo: list[str] = []
    numero = 0

    def cerrar_parrafo() -> None:
        if parrafo:
            bloques.append(" ".join(parrafo))
            parrafo.clear()

    def poner_imagenes(hasta: float | None) -> None:
        nonlocal numero
        while imagenes and (hasta is None or imagenes[0]["t"] < hasta):
            cerrar_parrafo()
            numero += 1
            bloques.append(f"![Imagen {numero}]({imagenes.pop(0)['file']})")

    ordenados = sorted(segmentos, key=lambda s: s["index"])
    for i, seg in enumerate(ordenados):
        if i == 0:
            poner_imagenes(seg["t0"])
        parrafo.append(seg["text"].strip())
        siguiente = ordenados[i + 1]["t0"] if i + 1 < len(ordenados) else None
        poner_imagenes(siguiente)
    poner_imagenes(None)
    cerrar_parrafo()
    return "\n\n".join([f"# {titulo}", *bloques]) + "\n"


def unir_lineas(markdown: str) -> str:
    """Re-une los párrafos que un editor partió en varias líneas (Qt corta a 80 columnas al exportar)."""
    bloques = re.split(r"\n\s*\n", markdown.replace("\r\n", "\n").strip())
    resultado = []
    for bloque in bloques:
        lineas = bloque.split("\n")
        if len(lineas) > 1 and not any(INICIO_DE_BLOQUE.match(l.lstrip()) for l in lineas):
            bloque = " ".join(l.strip() for l in lineas)
        resultado.append(bloque)
    return "\n\n".join(resultado) + "\n"


def numerar_imagenes(markdown: str) -> str:
    """Reescribe el texto alternativo como «Imagen N» según el orden de aparición."""
    numero = 0

    def renombrar(linea: str) -> str:
        nonlocal numero
        m = IMAGEN.match(linea)
        if not m:
            return linea
        numero += 1
        return f"![Imagen {numero}]({m.group(2)})"

    return "\n".join(renombrar(l) for l in markdown.split("\n"))


def imagenes(markdown: str) -> list[str]:
    """Rutas de las imágenes en el orden en que aparecen."""
    return [m.group(2).strip("<>") for l in markdown.split("\n") if (m := IMAGEN.match(l.strip()))]


def _sin_titulo(markdown: str) -> list[str]:
    lineas = markdown.strip().split("\n")
    if lineas and lineas[0].startswith("# "):
        lineas = lineas[1:]
    return lineas


def para_chat(markdown: str) -> str:
    """Texto para pegar en un chat: sin título, cada imagen como «[Imagen N]» en su sitio."""
    numero = 0
    salida = []
    for linea in _sin_titulo(unir_lineas(markdown)):
        if IMAGEN.match(linea.strip()):
            numero += 1
            salida.append(f"[Imagen {numero}]")
        else:
            salida.append(linea)
    return "\n".join(salida).strip() + "\n"


def solo_texto(markdown: str) -> str:
    """Solo el texto, sin título ni imágenes."""
    lineas = [l for l in _sin_titulo(unir_lineas(markdown)) if not IMAGEN.match(l.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip() + "\n"


def con_rutas_absolutas(markdown: str, carpeta: Path) -> str:
    """Para agentes: las imágenes apuntan a su ruta absoluta en disco (entre <> por los espacios)."""
    def absoluta(linea: str) -> str:
        m = IMAGEN.match(linea.strip())
        if not m:
            return linea
        ruta = Path(m.group(2).strip("<>"))
        ruta = ruta if ruta.is_absolute() else carpeta / ruta
        return f"![{m.group(1)}](<{ruta.as_posix()}>)"

    return "\n".join(absoluta(l) for l in unir_lineas(markdown).split("\n"))
