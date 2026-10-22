"""Arranque y cierre de los servicios locales (voice-core y tasks-service) desde el overlay, cuando es su dueño."""
from __future__ import annotations

import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from voice_core.ajustes import Ajustes

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


class ServicioLocal:
    def __init__(self, aj: Ajustes, ruta_config: Path, modulo: str = "voice_core", nombre: str = "voice-core",
                 base: str | None = None, lanzar: bool | None = None) -> None:
        self._aj = aj
        self._ruta_config = ruta_config
        self._modulo = modulo
        self.nombre = nombre
        self._base = base or aj.url_voice_core
        self._lanzar = aj.overlay.lanzar_servicio if lanzar is None else lanzar
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def propio(self) -> bool:
        return self._proc is not None

    def responde(self, espera_s: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(self._base + "/health", timeout=espera_s):
                return True
        except OSError:
            return False

    def asegurar(self, espera_max_s: float = 30.0) -> bool:
        """Si el servicio no responde y la configuración lo permite, lo lanza oculto y espera a que conteste."""
        if self.responde():
            log.info("%s ya estaba en marcha: el overlay solo se conecta", self.nombre)
            return True
        if not self._lanzar:
            return False
        python = Path(sys.executable).with_name("python.exe")  # python.exe + CREATE_NO_WINDOW: consola oculta y E/S válida
        salida = open(self._aj.carpeta_datos / f"{self.nombre}.salida.log", "ab")  # noqa: SIM115  # vive con el proceso
        self._proc = subprocess.Popen([str(python), "-m", self._modulo, "--config", str(self._ruta_config)],
                                      cwd=str(self._aj.raiz), stdout=salida, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        log.info("%s lanzado oculto (pid %d)", self.nombre, self._proc.pid)
        fin = time.monotonic() + espera_max_s
        while time.monotonic() < fin:
            if self._proc.poll() is not None:
                log.error("%s terminó al arrancar (código %s); ver datos\\%s.salida.log", self.nombre,
                          self._proc.returncode, self.nombre)
                self._proc = None
                return False
            if self.responde(0.5):
                return True
            time.sleep(0.25)
        return False

    def cerrar(self, espera_s: float = 20.0) -> None:
        """Cierre ordenado (POST /shutdown) del servicio que lanzamos; si no responde, se termina el proceso."""
        if self._proc is None:
            return
        try:
            urllib.request.urlopen(urllib.request.Request(self._base + "/shutdown", method="POST"), timeout=3)
            self._proc.wait(timeout=espera_s)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("%s no se cerró por la API (%s): se termina el proceso", self.nombre, e)
            self._proc.terminate()
            self._proc.wait(timeout=5)
        log.info("%s cerrado (código %s)", self.nombre, self._proc.returncode)
        self._proc = None
