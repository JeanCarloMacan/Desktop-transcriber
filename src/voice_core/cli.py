"""Cliente de línea de comandos para voice-core (mientras no hay overlay).

Uso:  voice-core-cli estado | modo <auto|gpu|cpu|pausado> | liberar | iniciar [--tarea] | detener | alternar [--tarea]
      voice-core-cli sesiones [-n 10] | exportar <id> [--txt] | seguir [--after N] [--lineas]

`seguir` solo muestra los eventos: cerrarlo con Ctrl+C no detiene ni pausa el servicio.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

BASE = os.environ.get("TRT_URL", "http://127.0.0.1:8765").rstrip("/")
ESC = "\x1b["
TENUE, NORMAL = f"{ESC}2m", f"{ESC}0m"


class VistaParrafo:
    """Muestra cada sesión como un párrafo continuo.

    Las líneas completas del párrafo se escriben una sola vez; la última línea y el texto provisional (tenue, debajo)
    forman la «zona viva», que se borra y redibuja con movimientos relativos del cursor. Se envuelve a `ancho - 1`
    columnas para que la terminal nunca parta líneas por su cuenta y la cuenta de líneas sea exacta.
    """

    def __init__(self, escribir: Callable[[str], None], ancho: int) -> None:
        self._w = escribir
        self._ancho = max(30, ancho - 1)
        self._reiniciar()

    def _reiniciar(self) -> None:
        self._parrafo = ""
        self._parcial = ""
        self._fijas = 0
        self._vivas = 0

    def _borrar(self) -> None:
        if self._vivas:
            subir = f"{ESC}{self._vivas - 1}A" if self._vivas > 1 else ""
            self._w(f"\r{subir}{ESC}J")
            self._vivas = 0

    def _dibujar(self) -> None:
        self._borrar()
        lineas = textwrap.wrap(self._parrafo, self._ancho)
        for linea in lineas[self._fijas:-1]:
            self._w(linea + "\n")
        self._fijas = max(self._fijas, len(lineas) - 1)
        vivas = lineas[-1:]
        if self._parcial:
            vivas += [TENUE + l + NORMAL for l in textwrap.wrap("… " + self._parcial, self._ancho)]
        self._w("\n".join(vivas))
        self._vivas = len(vivas)

    def parcial(self, texto: str) -> None:
        self._parcial = texto
        self._dibujar()

    def final(self, texto: str) -> None:
        self._parrafo = f"{self._parrafo} {texto}".strip()
        self._parcial = ""
        self._dibujar()

    def linea(self, texto: str) -> None:
        """Una línea suelta (estado del motor, errores) sin perder el párrafo en curso."""
        self._borrar()
        self._w(texto + "\n")
        self._dibujar()

    def cerrar(self, pie: str | None = None) -> None:
        self._parcial = ""
        self._dibujar()
        if self._vivas:
            self._w("\n")
        self._reiniciar()
        if pie:
            self._w(pie + "\n")


def _activar_vt() -> None:
    """Las consolas clásicas de Windows necesitan activar el modo VT para entender las secuencias ANSI."""
    if os.name != "nt":
        return
    import ctypes

    k32 = ctypes.windll.kernel32
    salida = k32.GetStdHandle(-11)
    modo = ctypes.c_uint32()
    if k32.GetConsoleMode(salida, ctypes.byref(modo)):
        k32.SetConsoleMode(salida, modo.value | 0x0004)


def _escribir(texto: str) -> None:
    sys.stdout.write(texto + "\n")
    sys.stdout.flush()


def _pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> Any:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(BASE + ruta, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json"} if datos else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            contenido = r.read().decode("utf-8")
            return json.loads(contenido) if "json" in r.headers.get("Content-Type", "") else contenido
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{e.code}: {e.read().decode('utf-8')}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"voice-core no responde en {BASE} ({e.reason})") from None


def _describir(ev: dict) -> str | None:
    tipo = ev["type"]
    if tipo == "session.started":
        return f"● sesión {ev['session_id']} ({ev['mode']})"
    if tipo == "session.finalized":
        return f"■ {ev['segments']} frases · {ev['duration_s']:.0f} s · sesión {ev['session_id']}"
    if tipo == "engine.state":
        return f"motor: {ev['state']} ({ev['device'] or '—'}) · {ev['reason']}"
    if tipo == "engine.error":
        return f"ERROR: {ev['message']}"
    return None


def _seguir(despues: int | None, por_lineas: bool) -> None:
    """Muestra los eventos en vivo. Si el servicio se reinicia, reconecta solo y recupera lo que se perdió."""
    import time

    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    def escribir(texto: str) -> None:
        sys.stdout.write(texto)
        sys.stdout.flush()

    _activar_vt()
    vista = VistaParrafo(escribir, shutil.get_terminal_size((100, 30)).columns)

    def mostrar(ev: dict) -> None:
        tipo = ev["type"]
        if por_lineas:
            if tipo == "segment.final":
                _escribir(f"  [{ev['index']}] {ev['text']}")
            elif (texto := _describir(ev)) is not None:
                _escribir(texto)
        elif tipo == "segment.partial":
            vista.parcial(ev["text"])
        elif tipo == "segment.final":
            vista.final(ev["text"])
        elif tipo in ("session.started", "session.finalized"):
            vista.cerrar(_describir(ev))
        elif (texto := _describir(ev)) is not None:
            vista.linea(texto)

    _escribir(f"Visor de {BASE}\nCtrl+C cierra solo este visor; el servicio sigue (para liberar la GPU: Ctrl+Alt+L).")
    ultimo, caido = despues, False
    while True:
        url = BASE.replace("http", "ws") + "/ws" + (f"?after={ultimo}" if ultimo is not None else "")
        try:
            with connect(url, open_timeout=3) as ws:
                vista.linea("Conectado al servicio." if not caido else "Servicio disponible de nuevo.")
                caido = False
                for crudo in ws:
                    ev = json.loads(crudo)
                    if ev.get("seq") is not None:
                        ultimo = ev["seq"]
                    mostrar(ev)
        except (OSError, TimeoutError, ConnectionClosed):
            if not caido:
                vista.linea("Servicio no disponible: reintento cada 2 s…")
                caido = True
            time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(prog="voice-core-cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado")
    m = sub.add_parser("modo")
    m.add_argument("modo", choices=["auto", "gpu", "cpu", "pausado"])
    sub.add_parser("liberar")
    for nombre in ("iniciar", "alternar"):
        p = sub.add_parser(nombre)
        p.add_argument("--tarea", action="store_true")
    sub.add_parser("detener")
    s = sub.add_parser("sesiones")
    s.add_argument("-n", type=int, default=10)
    e = sub.add_parser("exportar")
    e.add_argument("id")
    e.add_argument("--txt", action="store_true")
    f = sub.add_parser("seguir")
    f.add_argument("--after", type=int, default=None)
    f.add_argument("--lineas", action="store_true", help="una línea por frase en lugar de un párrafo continuo")
    a = ap.parse_args()

    if a.cmd == "estado":
        _escribir(json.dumps(_pedir("GET", "/engine"), ensure_ascii=False, indent=2))
    elif a.cmd == "modo":
        _escribir(json.dumps(_pedir("PUT", "/engine/mode", {"mode": a.modo}), ensure_ascii=False, indent=2))
    elif a.cmd == "liberar":
        _escribir(json.dumps(_pedir("POST", "/engine/release"), ensure_ascii=False, indent=2))
    elif a.cmd in ("iniciar", "alternar"):
        ruta = "/sessions" if a.cmd == "iniciar" else "/sessions/toggle"
        r = _pedir("POST", ruta, {"mode": "tarea" if a.tarea else "dictado"})
        _escribir(f"{r['id']} · {r['status']}")
    elif a.cmd == "detener":
        activas = _pedir("GET", "/sessions?status=grabando&limit=1")
        if not activas:
            raise SystemExit("No hay sesión grabando")
        r = _pedir("POST", f"/sessions/{activas[0]['id']}/stop")
        _escribir(f"{r['id']} · {r['status']}")
    elif a.cmd == "sesiones":
        for r in _pedir("GET", f"/sessions?limit={a.n}"):
            _escribir(f"{r['id']}  {r['started_at'][:19]}  {r['mode']:<7} {r['status']:<10} {r['text'][:70]}")
    elif a.cmd == "exportar":
        _escribir(_pedir("GET", f"/sessions/{a.id}/export?format={'txt' if a.txt else 'md'}"))
    elif a.cmd == "seguir":
        try:
            _seguir(a.after, a.lineas)
        except KeyboardInterrupt:
            _escribir("\nVisor cerrado. El servicio sigue corriendo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
