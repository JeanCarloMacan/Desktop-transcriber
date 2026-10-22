"""Cliente de voice-core para el overlay: WebSocket con reconexión y peticiones REST, en hilos aparte.

Los resultados llegan a la interfaz por señales de Qt (conexión en cola, segura entre hilos).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class Cliente(QObject):
    evento = Signal(dict)
    conexion = Signal(bool)
    respuesta = Signal(str, object)  # (operación, dict | list | str | Exception)

    def __init__(self, base: str) -> None:
        super().__init__()
        self._base = base.rstrip("/")
        self._parar = threading.Event()
        self._ws: Any = None

    # ------------------------------------------------------------ REST

    def pedir(self, operacion: str, metodo: str, ruta: str, cuerpo: dict | None = None) -> None:
        threading.Thread(target=self._pedir, args=(operacion, metodo, ruta, cuerpo), daemon=True,
                         name=f"rest-{operacion}").start()

    def subir(self, operacion: str, ruta: str, datos: bytes, tipo: str) -> None:
        """POST de un cuerpo binario (p. ej. una captura PNG) en otro hilo."""
        threading.Thread(target=self._pedir, args=(operacion, "POST", ruta, None, (datos, tipo)), daemon=True,
                         name=f"rest-{operacion}").start()

    def pedir_ya(self, metodo: str, ruta: str, cuerpo: dict | None = None, espera_s: float = 5,
                 binario: tuple[bytes, str] | None = None) -> Any:
        """Petición síncrona (para el arranque y la salida)."""
        if binario is not None:
            datos, cabeceras = binario[0], {"Content-Type": binario[1]}
        else:
            datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
            cabeceras = {"Content-Type": "application/json"} if datos else {}
        req = urllib.request.Request(self._base + ruta, data=datos, method=metodo, headers=cabeceras)
        with urllib.request.urlopen(req, timeout=espera_s) as r:
            texto = r.read().decode("utf-8")
            return json.loads(texto) if "json" in r.headers.get("Content-Type", "") else texto

    def _pedir(self, operacion: str, metodo: str, ruta: str, cuerpo: dict | None,
               binario: tuple[bytes, str] | None = None) -> None:
        try:
            resultado: Any = self.pedir_ya(metodo, ruta, cuerpo, espera_s=30 if binario else 5, binario=binario)
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "replace")
            try:
                detalle = json.loads(detalle).get("detail", detalle)
            except (ValueError, AttributeError):
                pass
            resultado = RuntimeError(f"{e.code}: {detalle}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            resultado = e
        self.respuesta.emit(operacion, resultado)

    # ------------------------------------------------------------ WebSocket

    def iniciar(self) -> None:
        threading.Thread(target=self._escuchar, daemon=True, name="ws-overlay").start()

    def detener(self) -> None:
        self._parar.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except OSError:
                pass

    def _escuchar(self) -> None:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        ultimo: int | None = None
        conectado = False
        while not self._parar.is_set():
            url = self._base.replace("http", "ws", 1) + "/ws" + (f"?after={ultimo}" if ultimo is not None else "")
            try:
                with connect(url, open_timeout=3) as ws:
                    self._ws = ws
                    conectado = True
                    self.conexion.emit(True)
                    for crudo in ws:
                        ev = json.loads(crudo)
                        if ev.get("seq") is not None:
                            ultimo = ev["seq"]
                        self.evento.emit(ev)
            except (OSError, TimeoutError, ConnectionClosed) as e:
                log.debug("WebSocket: %s", e)
            finally:
                self._ws = None
            if conectado:
                conectado = False
                self.conexion.emit(False)
            if not self._parar.wait(2):
                continue
