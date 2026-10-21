"""El visor muestra cada sesión como un párrafo continuo y reemplaza el texto provisional en el sitio."""
from __future__ import annotations

import re

from voice_core.cli import VistaParrafo


class Terminal:
    """Emulador mínimo: texto, \\r, \\n, ESC[nA (subir), ESC[J (borrar hasta el final) y colores (se ignoran)."""

    def __init__(self, ancho: int) -> None:
        self.ancho = ancho
        self.lineas: list[str] = [""]
        self.fila = 0
        self.col = 0

    def escribir(self, texto: str) -> None:
        for token in re.findall(r"\x1b\[\d*[A-Za-z]|\r|\n|[^\x1b\r\n]+", texto):
            if token == "\r":
                self.col = 0
            elif token == "\n":
                self.fila += 1
                self.col = 0
                if self.fila == len(self.lineas):
                    self.lineas.append("")
            elif token.startswith("\x1b["):
                cmd, n = token[-1], token[2:-1]
                if cmd == "A":
                    self.fila -= int(n or 1)
                elif cmd == "J":
                    self.lineas[self.fila] = self.lineas[self.fila][: self.col]
                    del self.lineas[self.fila + 1:]
            else:
                linea = self.lineas[self.fila]
                self.lineas[self.fila] = linea[: self.col] + token + linea[self.col + len(token):]
                self.col += len(token)
                assert self.col <= self.ancho, "la vista nunca debe dejar que la terminal parta una línea"

    @property
    def pantalla(self) -> str:
        return "\n".join(self.lineas).rstrip("\n")


def test_las_frases_forman_un_parrafo_continuo() -> None:
    t = Terminal(40)
    v = VistaParrafo(t.escribir, 40)
    frases = ["Hola, estoy haciendo la prueba en vivo.", "En este momento una prueba de dictado.",
              "Parece que tiene algunos errores.", "Pero se corrige solo."]
    for f in frases:
        v.parcial(f[:10])
        v.parcial(f[:20])
        v.final(f)
    v.cerrar()
    assert " ".join(t.pantalla.split()) == " ".join(frases)
    assert all(len(l) <= 39 for l in t.lineas)


def test_el_parcial_se_muestra_y_luego_desaparece() -> None:
    t = Terminal(60)
    v = VistaParrafo(t.escribir, 60)
    v.final("Primera frase.")
    v.parcial("segunda frase todavía provisional")
    assert "… segunda frase todavía provisional" in t.pantalla
    v.final("Segunda frase.")
    assert "provisional" not in t.pantalla
    assert t.pantalla == "Primera frase. Segunda frase."


def test_un_parcial_largo_que_ocupa_varias_lineas_se_borra_entero() -> None:
    t = Terminal(30)
    v = VistaParrafo(t.escribir, 30)
    v.final("Inicio.")
    v.parcial("palabra " * 12)
    assert len(t.lineas) >= 4
    v.final("Fin.")
    assert t.pantalla == "Inicio. Fin."


def test_lineas_sueltas_no_rompen_el_parrafo() -> None:
    t = Terminal(50)
    v = VistaParrafo(t.escribir, 50)
    v.final("Una frase.")
    v.linea("motor: en_espera (cuda)")
    v.final("Otra frase.")
    v.cerrar("■ 2 frases")
    assert t.pantalla.splitlines() == ["motor: en_espera (cuda)", "Una frase. Otra frase.", "■ 2 frases"]
