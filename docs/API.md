# API

Dos servicios locales (solo escuchan en la propia máquina y no tienen autenticación). Mientras corren, la
documentación interactiva generada por FastAPI está en `/docs` de cada uno.

| Servicio | Dirección | Qué hace | Contratos formales |
|---|---|---|---|
| **voice-core** | `http://127.0.0.1:8765` | dictados, capturas y documento de cada sesión | [`openapi.json`](../contracts/openapi.json) · [`eventos.schema.json`](../contracts/eventos.schema.json), desde `src/voice_core/contratos.py` |
| **tasks-service** | `http://127.0.0.1:8766` | tareas por categoría extraídas de los dictados con LM Studio | [`tareas.openapi.json`](../contracts/tareas.openapi.json) · [`tareas.eventos.schema.json`](../contracts/tareas.eventos.schema.json), desde `src/tasks_service/contratos.py` |

Los errores propios llegan como `{"detail": "…"}`; los 422 de validación, con el formato estándar de FastAPI (una
lista en `detail`).

## voice-core: REST

| Método y ruta | Cuerpo / parámetros | Respuesta | Errores |
|---|---|---|---|
| `GET /health` | — | `{"status": "ok", "version": "0.4.0"}` | — |
| `GET /engine` | — | `EngineStatusOut` | — |
| `PUT /engine/mode` | `{"mode": "auto" \| "gpu" \| "cpu" \| "pausado"}` | `EngineStatusOut` | 422 |
| `POST /engine/release` | `?stop_session=true` para cerrar antes el dictado en curso | `EngineStatusOut` | 409 si está dictando y no se pidió `stop_session` |
| `POST /sessions` | `{"mode": "dictado" \| "tarea"}` | `SessionOut` (201) | 409: ya hay sesión, motor en pausa o sin GPU en modo `gpu` |
| `POST /sessions/toggle` | `{"mode": …}` | `SessionOut` | 409 |
| `POST /sessions/{id}/stop` | — | `SessionOut` (`procesando`; publica `session.stopped` y el texto final llega con `session.finalized`) | 404, 409 |
| `GET /sessions` | `?limit=20&status=final` | `list[SessionOut]` (más recientes primero) | 422 |
| `GET /sessions/{id}` | — | `SessionDetailOut` (con `segments`, `audio_path` y `attachment_items`) | 404 |
| `GET /sessions/{id}/export` | `?format=md \| chat \| txt` | texto (ver [Exportar](#exportar)) | 404, 422 |
| `POST /sessions/{id}/attachments` | cuerpo: la PNG en bruto · `?t=12.4` opcional | `AttachmentOut` (201) | 404, 413 (> 25 MB), 415 (no es PNG) |
| `GET /sessions/{id}/attachments/{aid}` | — | la imagen (`image/png`) | 404 |
| `GET /sessions/{id}/document` | — | `DocumentOut` (el editado o, si no hay, el generado) | 404 |
| `PUT /sessions/{id}/document` | `{"markdown": "…"}` | `DocumentOut` | 404, 409 si la sesión sigue grabando |
| `DELETE /sessions/{id}/document` | — | `DocumentOut`: vuelve al documento generado | 404 |
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
  "id": "5b2e8c0d41a7",
  "mode": "dictado",
  "status": "final",
  "started_at": "2026-10-21T21:52:32.483000+00:00",
  "ended_at": "2026-10-21T21:55:01.527000+00:00",
  "text": "Listo, parece que inicia muy bien esta parte. …",
  "device": "cuda",
  "model": "large-v3",
  "attachments": 2,
  "edited_at": "2026-10-21T22:03:02.972000+00:00"
}
```

`text` es siempre la transcripción tal cual; la versión corregida en el editor está en `/document`. `attachments` es el
número de capturas y `edited_at`, la última corrección (`null` si no se tocó).

### Capturas

Se suben con el cuerpo en bruto (no `multipart`); se valida que sea una PNG por su firma. `t` son los segundos desde
el inicio de la sesión: si se omite, durante el dictado es el instante actual y, si ya terminó, el final. La imagen se
guarda en `datos/sesiones/<id>/capturas/NNN.png`.

```json
{
  "id": 1,
  "session_id": "3f9a1c2b7d40",
  "kind": "imagen",
  "t": 13.45,
  "file": "capturas/001.png",
  "path": "C:\\…\\datos\\sesiones\\3f9a1c2b7d40\\capturas\\001.png",
  "url": "/sessions/3f9a1c2b7d40/attachments/1",
  "width": 241,
  "height": 281,
  "created_at": "2026-10-22T00:00:43.471000+00:00"
}
```

### Documento

El documento de una sesión es Markdown: un título, los párrafos del dictado y las capturas **tras la frase que se
estaba diciendo** cuando se tomaron (después del último segmento con `t0 ≤ t`). Mientras no se edite, se genera a
partir de segmentos y capturas; al guardarlo con `PUT`, se conserva tal cual (con los párrafos re-unidos y las
imágenes renumeradas) y `DELETE` lo devuelve al generado. Cada cambio reescribe `documento.md` en la carpeta de la
sesión.

```json
{
  "session_id": "3f9a1c2b7d40",
  "markdown": "# Dictado · 2026-10-21 19:00\n\nRevisa la figura 3…\n\n![Imagen 1](capturas/001.png)\n\nAdemás…\n",
  "edited": true,
  "edited_at": "2026-10-22T00:03:02.972000+00:00",
  "folder": "C:\\…\\datos\\sesiones\\3f9a1c2b7d40",
  "images": 1
}
```

Las rutas de imagen del `markdown` son relativas a `folder`. Una captura que llega después de editar se añade al
final del documento editado.

### Exportar

| `format` | Para | Contenido |
|---|---|---|
| `md` (por defecto) | agentes que leen archivos | el documento con **rutas absolutas** a las PNG (`![Imagen 1](<C:/…/001.png>)`) |
| `chat` | pegar en un chat y arrastrar las imágenes aparte | sin título; cada imagen como `[Imagen N]` |
| `txt` | solo texto | sin título ni imágenes |

Todas parten del documento corregido si lo hay. tasks-service usa `txt` para leer el dictado que analiza.

## voice-core: eventos

Todos llevan `type`, `ts` (UTC) y `seq`. Los persistentes tienen `seq` creciente y sin huecos; `segment.partial` es
**efímero** (`seq: null`) y solo viaja por WebSocket.

| `type` | Campos | Cuándo |
|---|---|---|
| `session.started` | `session_id`, `mode` | al empezar a grabar |
| `segment.partial` | `session_id`, `text` | cada ~0,4 s mientras hablas (solo en GPU); reemplaza al anterior |
| `segment.final` | `session_id`, `index`, `t0`, `t1`, `text` | tras cada pausa de ~1 s |
| `session.stopped` | `session_id` | al cerrar el micrófono (al parar el dictado): ya no se escucha, aunque el modelo aún esté cargando |
| `session.finalized` | `session_id`, `text`, `duration_s`, `segments` | al terminar de transcribir lo grabado, con el texto completo |
| `engine.state` | `state`, `mode`, `device`, `reason` | en cada cambio de estado del motor |
| `engine.error` | `message`, `session_id?` | errores del motor o del micrófono |
| `attachment.added` | `session_id`, `attachment` (`AttachmentOut`) | al subir una captura |
| `session.edited` | `session_id`, `edited_at` (`null` = se volvió al original) | al guardar o descartar una corrección |

```json
{"seq": 4, "ts": "2026-10-21T21:52:42.6+00:00", "type": "segment.final",
 "session_id": "5b2e8c0d41a7", "index": 0, "t0": 1.15, "t1": 8.7,
 "text": "Listo, parece que inicia muy bien esta parte."}
```

```json
{"seq": null, "ts": "2026-10-21T21:52:41.9+00:00", "type": "segment.partial",
 "session_id": "5b2e8c0d41a7", "text": "Listo, parece que inicia muy"}
```

### Garantías

- **Orden y sin huecos**: `seq` es global y creciente. Un consumidor que guarde el último `seq` visto puede
  reconectarse con `?after=<seq>` y recibirá exactamente lo que se perdió, antes de los eventos nuevos.
- **Orden de una sesión**: `session.started` → `segment.*` → `session.stopped` → `session.finalized`. Lo que quedaba
  por transcribir al parar (la última frase o, si el modelo aún cargaba, todo lo grabado) llega como `segment.final`
  entre `session.stopped` y `session.finalized`. Los `engine.state` se intercalan.
- **Cierre**: al apagarse el servicio, los WebSocket se cierran con código **1001**; un suscriptor demasiado lento
  (más de 2000 eventos pendientes) se desconecta con **1013**.
- **Una sesión a la vez**: `POST /sessions` con otra activa responde 409.

## tasks-service: REST

Organizador de tareas ([F4](F4_TAREAS.md)): consume los dictados de voice-core, le pide a un modelo local de LM Studio
que extraiga las tareas y las deja como **propuestas**; el LLM nunca escribe en la lista.

| Método y ruta | Cuerpo / parámetros | Respuesta | Errores |
|---|---|---|---|
| `GET /health` | — | `{"status": "ok", "version": "0.4.0", "voice_core": true}` (`voice_core` = conectado al WebSocket de voice-core) | — |
| `GET /llm` | — | `LlmOut` | — |
| `GET /categories` | — | `list[CategoryOut]` | — |
| `GET /tasks` | `?status=propuesta \| pendiente \| hecha \| descartada` · `?category=` · `?limit=500` | `list[TaskOut]` | 422 |
| `POST /tasks` | `TaskIn` | `TaskOut` (201, `pendiente`) | 422: campo no válido o categoría desconocida |
| `PATCH /tasks/{id}` | `TaskPatch` | `TaskOut` | 404, 422: categoría desconocida o `status` no admitido (volver a `propuesta`) |
| `POST /extractions` | `{"session_id": "…"}` | `ExtractionOut` (202, `en_cola`, `origin: manual`) | 404 si voice-core no tiene la sesión, 409 si aún no ha terminado, 503 si voice-core no responde |
| `GET /extractions` | `?session_id=` · `?limit=50` | `list[ExtractionOut]` (más recientes primero) | 422 |
| `GET /events` | `?after=0&limit=200` | eventos con `seq > after` | 422 |
| `WS /ws` | `?after=N` (opcional) | reenvía lo guardado con `seq > N` y sigue en vivo | — |
| `POST /shutdown` | — | 202 `{"status": "cerrando"}`: cierre ordenado | 409 si el proceso no lo admite |

`GET /tasks` ordena las `pendiente` por fecha límite (las que no tienen, al final), las `hecha` por `done_at` (la
última arriba) y el resto por creación, de la más reciente a la más antigua. `?category=` compara el nombre exacto.

### `TaskOut`

```json
{
  "id": 12,
  "title": "Mandar el informe a Marta",
  "category": "Trabajo",
  "due": "2026-10-23",
  "notes": null,
  "status": "propuesta",
  "session_id": "3f9a1c2b7d40",
  "extraction_id": 4,
  "quote": "tengo que mandarle el informe a Marta antes del viernes",
  "created_at": "2026-10-22T09:14:03.120000+00:00",
  "updated_at": "2026-10-22T09:14:03.120000+00:00",
  "done_at": null
}
```

`due` es `YYYY-MM-DD` o `null` (las fechas relativas se resuelven respecto al día del dictado). `session_id`,
`extraction_id` y `quote` (el fragmento del dictado del que sale) son `null` en las tareas creadas a mano.

`status`: `propuesta` (la dejó el modelo) → `pendiente` (aprobada) ⇄ `hecha` (`done_at` se fija al marcarla y se
borra al reabrirla); cualquiera puede pasar a `descartada`. Ninguna vuelve a `propuesta`.

### `TaskIn` y `TaskPatch`

```json
{"title": "Comprar pilas", "category": "Personal", "due": "2026-10-24", "notes": "las AA del mando"}
```

- `TaskIn`: `title` (1–200 caracteres) y `category` obligatorios; `due` y `notes` (hasta 4000) opcionales. La
  categoría tiene que ser una de `/categories` (sin distinguir mayúsculas; se guarda con su nombre configurado).
  Campos desconocidos → 422.
- `TaskPatch`: los mismos campos más `status` (`pendiente`, `hecha` o `descartada`), todos opcionales; solo cambian
  los presentes. `due` o `notes` a `null` los borran; `title`, `category` o `status` a `null` se ignoran. Aprobar una
  propuesta es `{"status": "pendiente"}`.

### Extracciones

Una extracción es una llamada al modelo sobre un dictado. Se crean de dos formas:

- **`auto`**: al llegar el `session.finalized` de un dictado en modo `tarea` (`Ctrl+Alt+T`) con texto. Una sola por
  sesión, y nunca para dictados anteriores a la instalación del organizador.
- **`manual`**: `POST /extractions` («Extraer tareas» en el editor), con cualquier dictado terminado. Repetir la
  extracción de una sesión **sustituye** sus propuestas pendientes (pasan a `descartada`); las aprobadas no se tocan.

Se procesan **de una en una**, en orden. El texto que se analiza es el corregido en el editor si lo hay
(`export?format=txt` de voice-core).

```json
{
  "id": 4,
  "session_id": "3f9a1c2b7d40",
  "origin": "auto",
  "status": "en_cola",
  "model": null,
  "tasks": 0,
  "duration_s": null,
  "error": null,
  "waiting": "la RTX 5090 está ocupada (3,2 GB en uso)",
  "created_at": "2026-10-22T09:13:41.502000+00:00"
}
```

| `status` | Significado |
|---|---|
| `en_cola` | esperando turno; con `waiting` ≠ `null`, esperando a que se libere la GPU de LM Studio |
| `procesando` | llamando al modelo; `model` ya está fijado y `waiting` vuelve a `null` |
| `lista` | `tasks` = cuántas propuso; `duration_s` = segundos de la llamada, carga del modelo incluida (`null` si el dictado no tenía texto) |
| `error` | `error` explica por qué (LM Studio no responde, respuesta inválida, voice-core caído…); se puede repetir con `POST /extractions` |

`waiting` es el motivo por el que una extracción sigue en cola (`null` si no espera); hoy, la **salvaguarda de la
GPU**: con `[tareas] gpu` configurada (vacía = sin comprobar), si el modelo no está cargado y esa GPU tiene más de
`gpu_vram_max_mib` (1024) de VRAM usada o más de `gpu_uso_max_pct` (10 %) de cómputo medio, no se pide la carga y se
vuelve a mirar cada `reintento_gpu_s` (60 s); p. ej. «la RTX 5090 está ocupada (3,2 GB en uso y 45 % de cómputo)».
Las extracciones que quedan en cola o a medias al cerrar el servicio se retoman al arrancar.

### `CategoryOut` y `LlmOut`

```json
[
  {"name": "Trabajo", "description": "proyectos, código, reuniones, correos y entregas del trabajo",
   "pending": 3, "proposed": 1, "configured": true},
  {"name": "Otros", "description": "lo que no encaja en ninguna de las demás",
   "pending": 0, "proposed": 0, "configured": true},
  {"name": "Casa", "description": "", "pending": 2, "proposed": 0, "configured": false}
]
```

Las categorías salen de `[tareas.categorias]`, en su orden, más **Otros** al final. Una categoría quitada de la
configuración sigue apareciendo mientras tenga tareas pendientes o propuestas (`configured: false`), pero ya no se
puede asignar.

```json
{"url": "http://127.0.0.1:1234", "model": "openai/gpt-oss-20b", "reachable": true, "loaded": false, "ttl_s": 120}
```

`reachable` = LM Studio responde; `loaded` = el modelo está cargado (`null` si LM Studio no responde); `ttl_s` = la red
de seguridad con la que LM Studio descarga el modelo si tasks-service no pudo hacerlo al vaciarse la cola.

## tasks-service: eventos

Mismo mecanismo que voice-core (`seq` propio, creciente y sin huecos; recuperación con `?after=`; cierre con 1001 y
1013), pero **todos son persistentes**: no hay efímeros.

| `type` | Campos | Cuándo |
|---|---|---|
| `task.created` | `session_id` (`null` si se creó a mano), `task` (`TaskOut`) | cada propuesta de una extracción y cada tarea añadida a mano |
| `task.updated` | `session_id`, `task` (`TaskOut`) | cada `PATCH` y cada propuesta sustituida al repetir una extracción |
| `extraction.updated` | `session_id`, `extraction` (`ExtractionOut`) | al encolarse, al empezar a esperar la GPU (con `waiting`), al pasar a `procesando` y al terminar (`lista` o `error`) |

```json
{"seq": 57, "ts": "2026-10-22T09:13:42.6+00:00", "type": "extraction.updated", "session_id": "3f9a1c2b7d40",
 "extraction": {"id": 4, "session_id": "3f9a1c2b7d40", "origin": "auto", "status": "en_cola", "model": null,
                "tasks": 0, "duration_s": null, "error": null,
                "waiting": "la RTX 5090 está ocupada (3,2 GB en uso)",
                "created_at": "2026-10-22T09:13:41.502+00:00"}}
```

## Ejemplos

### curl.exe (tasks-service)

```powershell
$t = "http://127.0.0.1:8766"
$json = "Content-Type: application/json"
curl.exe -s "$t/health"
curl.exe -s "$t/llm"
curl.exe -s "$t/categories"
curl.exe -s "$t/tasks?status=propuesta"
curl.exe -s -X PATCH "$t/tasks/12" -H $json -d '{\"status\": \"pendiente\"}'
curl.exe -s -X POST "$t/tasks" -H $json -d '{\"title\": \"Comprar pilas\", \"category\": \"Personal\"}'
curl.exe -s -X POST "$t/extractions" -H $json -d '{\"session_id\": \"3f9a1c2b7d40\"}'
curl.exe -s "$t/extractions?session_id=3f9a1c2b7d40"
curl.exe -s "$t/events?after=0"
curl.exe -s -X POST "$t/shutdown"
```

> En Windows PowerShell 5.1 las comillas dobles de un argumento para un programa externo se escriben `\"` (en
> PowerShell 7.3 o posterior, tal cual: `'{"status": "pendiente"}'`). Para un cuerpo con tildes («Investigación») es
> más seguro un archivo en UTF-8: `--data-binary "@tarea.json"`.

### PowerShell (voice-core)

```powershell
Invoke-RestMethod http://127.0.0.1:8765/engine
Invoke-RestMethod -Method Post http://127.0.0.1:8765/sessions -ContentType application/json -Body '{"mode":"dictado"}'
Invoke-RestMethod "http://127.0.0.1:8765/sessions?limit=5&status=final" | Select-Object id, text
Invoke-RestMethod "http://127.0.0.1:8765/sessions/<id>/export?format=md"
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/sessions/<id>/attachments" -ContentType image/png -InFile captura.png
Invoke-RestMethod "http://127.0.0.1:8765/sessions/<id>/document" | Select-Object -ExpandProperty markdown
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/engine/release?stop_session=true"
```

> Windows PowerShell 5.1 lee las respuestas JSON como ISO-8859-1 y estropea los acentos («nÃºmero»). El servicio
> responde en UTF-8: usa PowerShell 7 (`pwsh`), `curl.exe` o Python.

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

El mismo esquema sirve para tasks-service con `ws://127.0.0.1:8766/ws?after=…` y, por ejemplo, avisar de cada
`task.created` con `status: propuesta`. Así consume tasks-service a voice-core: guarda el último `seq` procesado en su
base (`state.voice_seq`) y no pierde dictados aunque estuviera apagado.

### Línea de comandos

`voice-core-cli` (incluido) cubre lo habitual: `estado`, `modo <m>`, `liberar`, `iniciar [--tarea]`, `detener`,
`alternar`, `sesiones [-n 10]`, `exportar <id> [--txt]` y `seguir [--after N] [--lineas]` (texto en vivo como párrafo).
Con `TRT_URL` se apunta a otro puerto. tasks-service no tiene CLI propia: se arranca con `tasks-service` (o
`python -m tasks_service`), con `--config` y `--puerto` opcionales.
