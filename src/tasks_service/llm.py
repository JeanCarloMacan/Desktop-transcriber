"""Cliente de LM Studio por su API compatible con OpenAI: carga el modelo a demanda (con TTL), obliga a responder
con un JSON Schema y lo descarga por la API REST v1. Síncrono: el servicio lo llama desde un hilo aparte."""
from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
LMS_POR_DEFECTO = Path.home() / ".lmstudio" / "bin" / "lms.exe"


class ErrorLlm(RuntimeError):
    """LM Studio no responde, no tiene el modelo o devolvió un error."""


@dataclass(frozen=True)
class Respuesta:
    contenido: str
    segundos: float
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    tokens_razonamiento: int | None = None


class ClienteLlm:
    def __init__(self, url: str, modelo: str, ttl_s: int = 600, espera_s: float = 300.0,
                 lms: Path | None = LMS_POR_DEFECTO, parametros: dict[str, Any] | None = None) -> None:
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.ttl_s = ttl_s
        self._espera_s = espera_s
        self._lms = lms
        self._parametros = {"temperature": 0, **(parametros or {})}

    # ------------------------------------------------------------ estado

    def responde(self, espera_s: float = 2.0) -> bool:
        try:
            self._json("GET", "/v1/models", espera_s=espera_s)
            return True
        except ErrorLlm:
            return False

    def cargado(self) -> bool | None:
        """True/False según LM Studio (API REST propia); None si no responde."""
        try:
            info = self._json("GET", "/api/v0/models/" + urllib.parse.quote(self.modelo, safe=""), espera_s=2.0)
        except ErrorLlm:
            return None
        return info.get("state") == "loaded"

    def asegurar_servidor(self, espera_max_s: float = 30.0) -> bool:
        """Si el servidor no responde, lo arranca con `lms server start` (sin abrir la ventana de LM Studio)."""
        if self.responde():
            return True
        if self._lms is None or not self._lms.is_file():
            return False
        log.info("LM Studio no responde: se arranca su servidor con %s", self._lms.name)
        try:
            subprocess.run([str(self._lms), "server", "start"], capture_output=True, timeout=espera_max_s,
                           creationflags=CREATE_NO_WINDOW, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("No se pudo arrancar el servidor de LM Studio: %s", e)
        fin = time.monotonic() + espera_max_s
        while time.monotonic() < fin:
            if self.responde():
                return True
            time.sleep(0.5)
        return False

    def descargar(self) -> int:
        """Descarga de LM Studio las instancias cargadas de este modelo (API REST v1); devuelve cuántas."""
        datos = self._json("GET", "/api/v1/models")
        instancias = [i["id"] for m in datos.get("models") or [] if m.get("key") == self.modelo
                      for i in m.get("loaded_instances") or []]
        for id_ in instancias:
            self._json("POST", "/api/v1/models/unload", {"instance_id": id_}, espera_s=30.0)
        return len(instancias)

    # ------------------------------------------------------------ generar

    def completar(self, mensajes: list[dict[str, str]], esquema: dict, nombre: str = "tareas") -> Respuesta:
        cuerpo = {
            "model": self.modelo,
            "messages": mensajes,
            "ttl": self.ttl_s,  # LM Studio descarga el modelo tras este tiempo sin uso
            "stream": False,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": nombre, "strict": True, "schema": esquema}},
            **self._parametros,
        }
        inicio = time.monotonic()
        datos = self._json("POST", "/v1/chat/completions", cuerpo, espera_s=self._espera_s)
        segundos = time.monotonic() - inicio
        try:
            contenido = datos["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ErrorLlm(f"Respuesta de LM Studio sin contenido: {str(datos)[:200]}") from e
        uso = datos.get("usage") or {}
        return Respuesta(contenido=contenido, segundos=segundos, tokens_entrada=uso.get("prompt_tokens"),
                         tokens_salida=uso.get("completion_tokens"),
                         tokens_razonamiento=(uso.get("completion_tokens_details") or {}).get("reasoning_tokens"))

    def _json(self, metodo: str, ruta: str, cuerpo: dict | None = None, espera_s: float = 5.0) -> dict:
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(self.url + ruta, data=datos, method=metodo,
                                     headers={"Content-Type": "application/json"} if datos else {})
        try:
            with urllib.request.urlopen(req, timeout=espera_s) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "replace")
            try:
                error = json.loads(detalle).get("error", detalle)
                detalle = error.get("message", detalle) if isinstance(error, dict) else error
            except (ValueError, AttributeError):
                pass
            raise ErrorLlm(f"LM Studio respondió {e.code}: {str(detalle)[:300]}") from e
        except TimeoutError as e:
            raise ErrorLlm(f"LM Studio no contestó en {espera_s:g} s") from e
        except (urllib.error.URLError, OSError) as e:
            raise ErrorLlm(f"LM Studio no responde en {self.url}") from e
        except ValueError as e:
            raise ErrorLlm(f"Respuesta de LM Studio que no es JSON: {e}") from e
