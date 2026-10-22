"""Lo que tasks-service necesita de voice-core por REST (síncrono; se llama desde un hilo aparte)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class VoiceCoreNoResponde(RuntimeError):
    pass


class SesionDesconocida(LookupError):
    pass


class ClienteVoiceCore:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def url_ws(self, despues: int) -> str:
        return self.base.replace("http", "ws", 1) + f"/ws?after={despues}"

    def sesion(self, id_: str) -> dict:
        return json.loads(self._get(f"/sessions/{urllib.parse.quote(id_, safe='')}", id_))

    def texto(self, id_: str) -> str:
        """El texto corregido si se editó en el editor; si no, la transcripción (sin título ni imágenes)."""
        return self._get(f"/sessions/{urllib.parse.quote(id_, safe='')}/export?format=txt", id_)

    def _get(self, ruta: str, id_: str) -> str:
        try:
            with urllib.request.urlopen(self.base + ruta, timeout=10) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SesionDesconocida(f"voice-core no tiene la sesión {id_}") from e
            raise VoiceCoreNoResponde(f"voice-core respondió {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise VoiceCoreNoResponde(f"voice-core no responde en {self.base}") from e
