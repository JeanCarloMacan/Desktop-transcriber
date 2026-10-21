# API de voice-core

Servicio local en `http://127.0.0.1:8765` (solo escucha en la propia máquina y no tiene autenticación). Mientras corre,
la documentación interactiva generada por FastAPI está en <http://127.0.0.1:8765/docs>.

Contratos formales, generados desde `src/voice_core/contratos.py`:
[`contracts/openapi.json`](../contracts/openapi.json) (REST) y
[`contracts/eventos.schema.json`](../contracts/eventos.schema.json) (eventos).

## REST

| Método y ruta | Cuerpo / parámetros | Respuesta | Errores |
|---|---|---|---|
| `GET /health` | — | `{"status": "ok", "version": "0.2.0"}` | — |
| `GET /engine` | — | `EngineStatusOut` | — |
| `PUT /engine/mode` | `{"mode": "auto" \| "gpu" \| "cpu" \| "pausado"}` | `EngineStatusOut` | 422 |
| `POST /engine/release` | `?stop_session=true` para cerrar antes el dictado en curso | `EngineStatusOut` | 409 si está dictando y no se pidió `stop_session` |
| `POST /sessions` | `{"mode": "dictado" \| "tarea"}` | `SessionOut` (201) | 409: ya hay sesión, motor en pausa o sin GPU en modo `gpu` |
| `POST /sessions/toggle` | `{"mode": …}` | `SessionOut` | 409 |
| `POST /sessions/{id}/stop` | — | `SessionOut` (`procesando`; el texto final llega con `session.finalized`) | 404, 409 |
| `GET /sessions` | `?limit=20&status=final` | `list[SessionOut]` (más recientes primero) | 422 |
| `GET /sessions/{id}` | — | `SessionDetailOut` (con `segments` y `audio_path`) | 404 |
| `GET /sessions/{id}/export` | `?format=md \| txt` | texto | 404, 422 |
| `GET /events` | `?after=0&limit=200` | `list[Evento]` persistentes con `seq > after` | 422 |
| `WS /ws` | `?after=N` (opcional) | reenvía lo guardado con `seq > N` y sigue en vivo | — |
| `POST /shutdown` | — | 202 `{"status": "cerrando"}`: cierre ordenado | 409 si el proceso no lo admite |

### `EngineStatusOut`

```json
{
  "state": "en_espera",
  "mode": "auto",
  "device": "cuda",
  "model": "large-v3",
  "idle_s": 15.2,
  "rest_in_s": 104.8,
  "auto_off_in_s": 1904.8,
  "vram_used_mib": 4410,
  "vram_free_mib": 11893
}
```

`state` ∈ `apagado`, `arrancando`, `dictando`, `en_espera`, `en_reposo`. `rest_in_s` = segundos hasta pasar el modelo
a RAM; `auto_off_in_s` = hasta el apagado completo; `vram_*` = la GPU permitida (todos los procesos).

### `SessionOut`

```json
{
  "id": "07e4b7d953e4",
  "mode": "dictado",
  "status": "final",
  "started_at": "2026-10-21T21:52:32.483000+00:00",
  "ended_at": "2026-10-21T21:55:01.527000+00:00",
  "text": "Listo, parece que inicia muy bien esta parte. …",
  "device": "cuda",
  "model": "large-v3"
}
```

## Eventos

Todos llevan `type`, `ts` (UTC) y `seq`. Los persistentes tienen `seq` creciente y sin huecos; `segment.partial` es
**efímero** (`seq: null`) y solo viaja por WebSocket.

| `type` | Campos | Cuándo |
|---|---|---|
| `session.started` | `session_id`, `mode` | al empezar a grabar |
| `segment.partial` | `session_id`, `text` | cada ~0,4 s mientras hablas (solo en GPU); reemplaza al anterior |
| `segment.final` | `session_id`, `index`, `t0`, `t1`, `text` | tras cada pausa de ~1 s |
| `session.finalized` | `session_id`, `text`, `duration_s`, `segments` | al cerrar la sesión, con el texto completo |
| `engine.state` | `state`, `mode`, `device`, `reason` | en cada cambio de estado del motor |
| `engine.error` | `message`, `session_id?` | errores del motor o del micrófono |

```json
{"seq": 4, "ts": "2026-10-21T21:52:42.6+00:00", "type": "segment.final",
 "session_id": "07e4b7d953e4", "index": 0, "t0": 1.15, "t1": 8.7,
 "text": "Listo, parece que inicia muy bien esta parte."}
```

```json
{"seq": null, "ts": "2026-10-21T21:52:41.9+00:00", "type": "segment.partial",
 "session_id": "07e4b7d953e4", "text": "Listo, parece que inicia muy"}
```

### Garantías

- **Orden y sin huecos**: `seq` es global y creciente. Un consumidor que guarde el último `seq` visto puede
  reconectarse con `?after=<seq>` y recibirá exactamente lo que se perdió, antes de los eventos nuevos.
- **Cierre**: al apagarse el servicio, los WebSocket se cierran con código **1001**; un suscriptor demasiado lento
  (más de 2000 eventos pendientes) se desconecta con **1013**.
- **Una sesión a la vez**: `POST /sessions` con otra activa responde 409.

## Ejemplos

### PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8765/engine
Invoke-RestMethod -Method Post http://127.0.0.1:8765/sessions -ContentType application/json -Body '{"mode":"dictado"}'
Invoke-RestMethod "http://127.0.0.1:8765/sessions?limit=5&status=final" | Select-Object id, text
Invoke-RestMethod "http://127.0.0.1:8765/sessions/<id>/export?format=md"
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/engine/release?stop_session=true"
```

### Python: consumidor que no pierde nada aunque se reinicie

```python
import json
import time
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

MARCA = Path("ultimo_seq.txt")  # dónde recuerda el consumidor por dónde iba


def procesar(evento: dict) -> None:
    if evento["type"] == "session.finalized" and evento["text"]:
        print(f"[{evento['session_id']}] {evento['text']}")


while True:
    despues = int(MARCA.read_text()) if MARCA.exists() else 0
    try:
        with connect(f"ws://127.0.0.1:8765/ws?after={despues}") as ws:
            for mensaje in ws:
                evento = json.loads(mensaje)
                procesar(evento)
                if evento["seq"] is not None:
                    MARCA.write_text(str(evento["seq"]))
    except (OSError, ConnectionClosed):
        time.sleep(2)  # el servicio no está o se está reiniciando
```

### Línea de comandos

`voice-core-cli` (incluido) cubre lo habitual: `estado`, `modo <m>`, `liberar`, `iniciar [--tarea]`, `detener`,
`alternar`, `sesiones [-n 10]`, `exportar <id> [--txt]` y `seguir [--after N] [--lineas]` (texto en vivo como párrafo).
Con `TRT_URL` se apunta a otro puerto.
