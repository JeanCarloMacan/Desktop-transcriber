"""Hotkeys globales de Windows con RegisterHotKey (no intercepta el resto del teclado ni necesita permisos)."""
from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
MODIFICADORES = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "mayus": 0x4, "win": 0x8}
TECLAS = {"space": 0x20, "espacio": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "pause": 0x13,
          **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parsear(combo: str) -> tuple[int, int]:
    """'ctrl+alt+space' → (modificadores, código de tecla virtual)."""
    partes = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not partes:
        raise ValueError("Hotkey vacía")
    mods = 0
    for p in partes[:-1]:
        if p not in MODIFICADORES:
            raise ValueError(f"Modificador desconocido en {combo!r}: {p}")
        mods |= MODIFICADORES[p]
    tecla = partes[-1]
    if tecla in TECLAS:
        return mods, TECLAS[tecla]
    if len(tecla) == 1 and tecla.isalnum():
        return mods, ord(tecla.upper())
    raise ValueError(f"Tecla desconocida en {combo!r}: {tecla}")


class Hotkeys:
    """Registra las combinaciones en un hilo con bucle de mensajes; los callbacks corren en ese hilo."""

    def __init__(self, atajos: dict[str, Callable[[], None]]) -> None:
        self._atajos = atajos
        self._hilo: threading.Thread | None = None
        self._id_hilo = 0
        self._listo = threading.Event()
        self.registradas: list[str] = []

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="hotkeys")
        self._hilo.start()
        self._listo.wait(timeout=5)

    def _bucle(self) -> None:
        user32 = ctypes.windll.user32
        self._id_hilo = ctypes.windll.kernel32.GetCurrentThreadId()
        por_id: dict[int, Callable[[], None]] = {}
        for i, (combo, accion) in enumerate(self._atajos.items(), start=1):
            try:
                mods, vk = parsear(combo)
            except ValueError as e:
                log.error("%s", e)
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                por_id[i] = accion
                self.registradas.append(combo)
                log.info("Hotkey registrada: %s", combo)
            else:
                log.warning("No se pudo registrar %s (¿la usa otra aplicación?)", combo)
        self._listo.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and (accion := por_id.get(msg.wParam)):
                    try:
                        accion()
                    except Exception:
                        log.exception("Fallo en la acción de la hotkey")
        finally:
            for i in por_id:
                user32.UnregisterHotKey(None, i)

    def detener(self) -> None:
        if self._hilo is not None and self._id_hilo:
            ctypes.windll.user32.PostThreadMessageW(self._id_hilo, WM_QUIT, 0, 0)
            self._hilo.join(timeout=2)
