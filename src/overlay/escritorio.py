"""Ayudas de Win32 para el overlay: ventana que no roba el foco, pegar en la ventana activa e instancia única."""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ERROR_ALREADY_EXISTS = 183
KEYEVENTF_KEYUP = 0x0002
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_V = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_kernel32.CreateMutexW.restype = wintypes.HANDLE

_mutex: int | None = None


def no_activar(hwnd: int) -> None:
    """Clics sin activar la ventana: el foco y el cursor se quedan en la aplicación que estás usando."""
    estilo = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, estilo | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


def es_no_activable(hwnd: int) -> bool:
    return bool(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE)


def modificadores_pulsados() -> bool:
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def enviar_ctrl_v(espera_max_s: float = 6.0) -> bool:
    """Pulsa Ctrl+V en la ventana activa. Primero espera a que sueltes Ctrl/Alt/Mayús/Win (vienes de pulsar
    Ctrl+Alt+V): si no, la aplicación recibiría Ctrl+Alt+V. Bloquea hasta `espera_max_s`; llamar fuera del hilo de la UI."""
    fin = time.monotonic() + espera_max_s
    while modificadores_pulsados():
        if time.monotonic() > fin:
            log.warning("No se soltaron las teclas modificadoras a tiempo: no se pega")
            return False
        time.sleep(0.02)
    for vk, flags in ((VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)):
        _user32.keybd_event(vk, 0, flags, 0)
    return True


def instancia_unica(nombre: str) -> bool:
    """True si somos la primera instancia. El mutex vive mientras viva el proceso."""
    global _mutex
    _mutex = _kernel32.CreateMutexW(None, False, f"Local\\{nombre}")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
