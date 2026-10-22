# Desktop Transcriber · Transcriptor RT

![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)
![faster-whisper](https://img.shields.io/badge/STT-faster--whisper%20large--v3-6D28D9)
![Local first](https://img.shields.io/badge/100%25-local-15803D)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

Dictado **local y en tiempo real** para Windows. Pulsas `Ctrl+Alt+D`, hablas, y el texto aparece en un panel flotante
encima de lo que estés revisando, **sin quitarle el foco**. Al terminar queda en el portapapeles y `Ctrl+Alt+V` lo pega
donde está el cursor (el chat de un modelo de IA, un editor, un correo…). Con `Ctrl+Alt+S` recortas la pantalla
mientras hablas y la captura queda **en el punto del texto donde la tomaste**; después corriges el dictado en un editor
y lo pasas a un chat o a un agente con sus imágenes. Con `Ctrl+Alt+T` dictas lo que tienes pendiente y un **modelo
local de LM Studio** lo convierte en **tareas por categoría** que apruebas en la ventana Tareas. Detrás hay
**servicios** con API REST y WebSocket, para que otros programas consuman tus dictados y tus tareas.

> **English summary.** Local, real-time dictation for Windows. Press `Ctrl+Alt+D`, speak, and the text streams into a
> floating always-on-top panel that never steals focus; when you stop, it is copied to the clipboard and `Ctrl+Alt+V`
> pastes it wherever your cursor is. `Ctrl+Alt+S` takes a screenshot (Windows Snipping Tool) that lands exactly where
> you were in the sentence; an editor lets you fix the text and hand it to an AI chat (text with `[Imagen N]` markers
> plus drag-and-drop of every image) or to a coding agent (Markdown with absolute image paths). `Ctrl+Alt+T` dictates a
> to-do note: a local LLM in LM Studio (`gpt-oss-20b`) turns it into categorized task proposals (with relative dates
> resolved) that you approve, edit or discard in a Tasks window; any other dictation can be sent to it from the editor.
> Speech recognition runs on your own GPU (faster-whisper `large-v3` + a custom glossary, ~3 % WER on technical Spanish
> dictation) or falls back to CPU. The model only occupies the GPU while you use it: 2 min on the GPU, then 30 min
> parked in RAM (wakes up in 0.4 s), then fully unloaded. The task model is only loaded when its GPU is not in use by
> another program (otherwise it waits and retries every minute) and is unloaded as soon as the queue is empty.
> Everything is exposed by local services (REST + WebSocket on `127.0.0.1:8765` for dictation and `:8766` for tasks)
> so other tools can consume your dictations and tasks. Docs are in Spanish.

![Panel flotante en tres estados](docs/img/panel.png)

## Qué hace

- **Texto en vivo** mientras hablas: provisional en gris cada ~0,4 s y definitivo tras cada pausa (~1 s).
- **Párrafo continuo** en un panel tipo nota adhesiva: siempre encima, translúcido, redimensionable, que no roba el foco.
- **Portapapeles automático** al terminar y **pegado con un atajo** en la ventana activa.
- **Capturas dentro del dictado**: `Ctrl+Alt+S` abre el recorte de Windows y la imagen queda tras la frase que estabas
  diciendo.
- **Editor de dictados** con las imágenes en su sitio y dos salidas para la IA: **chat** (texto con `[Imagen N]` y
  todas las imágenes de un solo arrastre) y **agente** (Markdown con rutas absolutas, más `documento.md` en la carpeta).
- **Tareas desde la voz**: un dictado con `Ctrl+Alt+T` pasa a un **LLM local de LM Studio** (`gpt-oss-20b`) que
  propone tareas por categoría, con su fecha resuelta («el viernes» → el día real); las apruebas, corriges o descartas
  en la ventana **Tareas**, y el modelo nunca escribe directamente en tus listas. Cualquier otro dictado tiene
  **«Extraer tareas»** en el editor.
- **Precisión con jerga técnica**: glosario propio (endpoint, Pydantic, n8n, LoRA…) → 3,2 % de palabras erróneas en
  dictados reales frente a 7,6 % sin glosario ([F0](docs/F0_SPIKE.md)).
- **Uso responsable de la GPU**: solo la GPU que elijas (nunca otra), el modelo se descarga solo cuando no lo usas y
  cede la memoria si otro programa la necesita. El modelo de tareas solo se carga si su GPU está libre y se descarga
  en cuanto no quedan extracciones.
- **Servicios reutilizables**: sesiones y tareas en SQLite, eventos con número de secuencia, API REST + WebSocket con
  contratos OpenAPI y JSON Schema versionados en el repositorio.
- **100 % local**: ni el audio ni el texto salen del equipo.

## Cómo funciona

```mermaid
flowchart LR
    mic(["Micrófono"]) --> sup
    atajos(["Ctrl+Alt+D · T · L"]) --> sup
    subgraph vc["voice-core · servicio local 127.0.0.1:8765"]
        direction TB
        sup["Supervisor<br/>FastAPI · SQLite · bus de eventos<br/>(nunca carga CUDA)"]
        wk["stt-worker (proceso hijo)<br/>VAD Silero + faster-whisper large-v3<br/>solo la GPU permitida, o CPU"]
        sup <-- "audio 16 kHz / texto" --> wk
    end
    sup -- "WebSocket /ws · REST" --> ov["Overlay<br/>panel · editor · ventana Tareas · bandeja"]
    sup -- "dictados de tarea" --> ts["tasks-service · 127.0.0.1:8766<br/>cola de extracciones · tareas en SQLite"]
    ts -- "prompt + JSON Schema" --> lm["LM Studio :1234<br/>gpt-oss-20b, carga a demanda"]
    ts -- "WebSocket /ws · REST" --> ov
    sup -- "WebSocket /ws · REST" --> otros["Otros servicios<br/>MCP · scripts"]
    ov -- "Ctrl+Alt+V" --> app["Ventana activa"]
```

El detalle (procesos, hilos, secuencia de un dictado, ciclo de vida del motor, base de datos) está en
[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md), y el del organizador de tareas en
[docs/F4_TAREAS.md](docs/F4_TAREAS.md#arquitectura).

## Requisitos

| | Mínimo | Probado |
|---|---|---|
| Sistema | Windows 10/11 | Windows 11 Pro |
| Python | 3.13 (lo instala [uv](https://docs.astral.sh/uv/)) | 3.13.9 |
| GPU (opcional) | NVIDIA con ≥ 3 GB libres y el [CUDA Toolkit 12.x](https://developer.nvidia.com/cuda-downloads) (para cuBLAS) | RTX 5080 (Blackwell), CUDA 12.9 |
| Sin GPU | funciona en CPU: solo texto final, ≈ 4–6 s por frase | i9-14900K |
| Disco | ≈ 3 GB (modelo) + ≈ 0,5 GB (entorno) | |
| Micrófono | cualquiera reconocido por Windows | |
| Tareas (opcional) | [LM Studio](https://lmstudio.ai/) con su servidor local y el modelo `openai/gpt-oss-20b` (≈ 12 GB de VRAM mientras extrae) | LM Studio en una RTX 5090 |

## Instalación

```powershell
git clone https://github.com/JeanCarloMacan/Desktop-transcriber.git
cd Desktop-transcriber
uv sync                                                    # crea .venv (≈ 480 MB, sin PyTorch)
.venv\Scripts\voice-core --descargar-modelo                # faster-whisper large-v3 (≈ 2,9 GB)
.venv\Scripts\pythonw -m overlay                           # panel + bandeja; arranca los servicios ocultos
```

Opcional:

```powershell
copy config\voice-core.example.toml config\voice-core.toml          # tu configuración (no se sube a git)
copy config\glosario.example.txt config\glosario.txt                # tu glosario de términos
powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1  # arrancar con Windows (-Quitar lo quita)
```

En `config/voice-core.toml`, ajusta sobre todo `motor.gpu`: parte del nombre de **la única GPU** que puede usar el motor
(por defecto `"5080"`) o su UUID (`nvidia-smi -L`). Si no coincide con ninguna, o con varias, el motor usa la CPU.

Para las **tareas** (opcional): instala [LM Studio](https://lmstudio.ai/), descarga en él el modelo
`openai/gpt-oss-20b` y deja activada la carga a demanda (JIT) de su servidor local; no hace falta dejar el modelo
cargado ni el servidor abierto (si no responde, tasks-service lo arranca con `lms server start`). En
`config/voice-core.toml`, `[tareas.categorias]` define tus categorías (nombre = descripción que ayuda al modelo) y
`[tareas] gpu`, la GPU que usa LM Studio para la salvaguarda (más abajo). Sin LM Studio, el dictado funciona igual.

## Uso

| Atajo | Hace |
|---|---|
| `Ctrl+Alt+D` | empieza o termina un dictado |
| `Ctrl+Alt+T` | empieza o termina un dictado de tareas: al terminar, el LLM propone sus tareas en la ventana Tareas |
| `Ctrl+Alt+V` | pega el último dictado donde está el cursor |
| `Ctrl+Alt+S` | recorta la pantalla y adjunta la imagen al dictado en curso (o al último) |
| `Ctrl+Alt+E` | abre el editor de dictados en el último, o lo oculta si ya está delante |
| `Ctrl+Alt+P` | abre la ventana Tareas (P de «pendientes»), o la oculta si ya está delante |
| `Ctrl+Alt+O` | muestra u oculta el panel |
| `Ctrl+Alt+L` | libera la GPU al instante |

En el panel: arrastrar lo mueve; los bordes y las esquinas cambian el tamaño; la rueda desplaza el texto (sin
arrastrarte si estás leyendo más arriba); `Ctrl` + rueda cambia la letra. Todo se recuerda. Al pasar el ratón por sus
iconos se ve qué hace cada uno y su atajo. El icono de la bandeja cambia de color con el estado y tiene un menú con el
modo del motor, el editor, las tareas, liberar la GPU y salir.

**Editor de dictados** (`Ctrl+Alt+E`, botón ✎ del panel o menú de la bandeja): lista de dictados, el documento con las
capturas en su sitio para corregirlo, «Insertar captura» en el cursor, **Copiar para chat** (pega el texto y arrastra
«N imágenes» al chat), **Copiar para agente** (Markdown con las rutas de las PNG) y **Extraer tareas** (manda el
dictado, ya corregido, al organizador). Detalle en [F3](docs/F3_EDITOR.md).

![Editor de dictados](docs/img/editor.png)

**Tareas** (`Ctrl+Alt+P`, botón ☑ del panel o menú de la bandeja): al terminar un dictado con `Ctrl+Alt+T` el panel
dice «Extrayendo tareas…» y después «N tareas propuestas». En la ventana, a la izquierda, **Propuestas (N)**, cada
categoría con sus pendientes y **Hechas**; en las propuestas, Aprobar, Aprobar todas, Editar, Descartar y «Ver
dictado»; en las listas, casilla para marcarla hecha, doble clic para editar (título, categoría, fecha, notas) y una
línea para añadir tareas a mano. Un dictado como «tengo que mandarle el informe a Marta el viernes, ah, y comprar pilas»
acaba en dos propuestas, la primera con la fecha de ese viernes. Las categorías se configuran en `[tareas.categorias]`
(siempre existe **Otros**). Detalle en [F4](docs/F4_TAREAS.md).

![Ventana de tareas con propuestas](docs/img/tareas.png)

**Ciclo de la GPU tras cada dictado:** 2 min con el modelo en la GPU (≈ 2,2 GB, volver a dictar es instantáneo) → 30 min
en RAM (≈ 0,3 GB en la GPU, despierta en 0,4 s) → apagado (0 GB). Todo configurable.

**Salvaguarda de la GPU en las tareas:** antes de cargar el modelo (≈ 12 GB), tasks-service mira por NVML la GPU que
usa LM Studio (`[tareas] gpu`, UUID o parte del nombre). Si otro programa la está usando (más de 1 GB de VRAM o más de
un 10 % de cómputo), la extracción espera con un aviso en el panel y se reintenta sola cada 60 s. El modelo se
descarga en cuanto se vacía la cola de extracciones; el TTL de LM Studio (`[tareas] ttl_s`) queda como red de
seguridad. Con `gpu = ""` (el valor de la plantilla) no se comprueba.

## Para otros servicios

```powershell
Invoke-RestMethod http://127.0.0.1:8765/engine                      # estado del motor
Invoke-RestMethod http://127.0.0.1:8765/sessions?limit=5            # últimos dictados
Invoke-RestMethod "http://127.0.0.1:8765/sessions/<id>/export?format=md"   # md (agente) · chat · txt
Invoke-RestMethod "http://127.0.0.1:8766/tasks?status=pendiente"           # tareas aprobadas (tasks-service)
```

```python
import json
from websockets.sync.client import connect

with connect("ws://127.0.0.1:8765/ws?after=0") as ws:   # ?after=N recupera lo que te perdiste
    for mensaje in ws:
        evento = json.loads(mensaje)
        if evento["type"] == "session.finalized":
            print(evento["text"])
```

Endpoints, eventos y ejemplos en [docs/API.md](docs/API.md) (los de tasks-service, también en
[F4](docs/F4_TAREAS.md)); los contratos formales están en
[`contracts/openapi.json`](contracts/openapi.json) y [`contracts/eventos.schema.json`](contracts/eventos.schema.json)
para la voz, y en [`contracts/tareas.openapi.json`](contracts/tareas.openapi.json) y
[`contracts/tareas.eventos.schema.json`](contracts/tareas.eventos.schema.json) para las tareas. Mientras los servicios
corren, la documentación interactiva queda en <http://127.0.0.1:8765/docs> y <http://127.0.0.1:8766/docs>.

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Reconocimiento de voz | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 1.2 + [CTranslate2](https://github.com/OpenNMT/CTranslate2) 4.8 (`large-v3`, `int8_float16`) |
| Detección de voz | Silero VAD v6 (ONNX Runtime, en CPU) |
| Audio | sounddevice (PortAudio/WASAPI) + soxr (remuestreo a 16 kHz) |
| Servicio | FastAPI + uvicorn + Pydantic v2, WebSocket con `websockets` |
| Tareas | [LM Studio](https://lmstudio.ai/) (API compatible con OpenAI, salida forzada con JSON Schema) + `openai/gpt-oss-20b` con `reasoning_effort=high` |
| Persistencia | SQLite (WAL) con migraciones SQL |
| GPU | NVML (`nvidia-ml-py`) para vigilar la VRAM sin crear contexto CUDA (también la GPU de LM Studio); `CUDA_VISIBLE_DEVICES` por UUID |
| Interfaz | PySide6 (Qt 6) + Win32 (`RegisterHotKey`, `WS_EX_NOACTIVATE`, `SendInput`) |
| Herramientas | uv, hatchling, pytest |

Versiones, motivos y licencias de cada pieza en [docs/STACK.md](docs/STACK.md).

## Estructura del repositorio

```
src/voice_core/     servicio de voz: API, motor, worker, segmentador, sesiones, documento, almacén, bus, hotkeys, CLI
src/tasks_service/  organizador de tareas: organizador (cola), parser (prompt + JSON Schema), llm (LM Studio),
                    gpu (salvaguarda), voz (cliente de voice-core), api, almacen, contratos, migraciones/
src/overlay/        panel flotante, bandeja, editor de dictados, ventana Tareas (tareas.py), capturas, clientes de los
                    servicios y ayudas de Win32
contracts/          OpenAPI y JSON Schema de los dos servicios, generados desde los modelos (un test vigila la deriva)
config/             plantillas de configuración y glosario (*.example.*)
docs/               arquitectura, API, stack, desarrollo, diarios de cada fase y diagramas
scripts/            exportar contratos, imágenes del panel, del editor y de las tareas, arranque con Windows
spikes/             experimentos que fundamentaron las decisiones (F0–F2 y el banco de modelos de F4)
tests/              tests unitarios (sin GPU ni LM Studio) y de punta a punta con el modelo real (-m gpu)
```

## Documentación

| Documento | Contenido |
|---|---|
| [ARQUITECTURA.md](docs/ARQUITECTURA.md) | componentes, procesos e hilos, secuencia de un dictado, ciclo de vida del motor, datos, decisiones |
| [API.md](docs/API.md) | REST, WebSocket, eventos y ejemplos |
| [STACK.md](docs/STACK.md) | tecnologías, versiones, por qué cada una y sus licencias |
| [DESARROLLO.md](docs/DESARROLLO.md) | entorno de desarrollo, tests, contratos, convenciones y solución de problemas |
| [F0 · motor](docs/F0_SPIKE.md) · [F1 · servicio](docs/F1_VOICE_CORE.md) · [F2 · panel](docs/F2_OVERLAY.md) · [F3 · editor](docs/F3_EDITOR.md) · [F4 · tareas](docs/F4_TAREAS.md) | diario de cada fase: especificación, mediciones, pruebas reales y correcciones |
| [Plan general](docs/planes/plan-transcriptor-rt.svg) · [Recursos y modos](docs/planes/recursos-y-modos.svg) | diagramas del plan y del uso de la GPU |
| [CHANGELOG.md](CHANGELOG.md) | cambios por versión |

## Hoja de ruta

- [x] **F0** · Elección del motor con mediciones reales (large-v3 + glosario).
- [x] **F1** · Servicio `voice-core`: captura, VAD, transcripción en vivo, sesiones, API y eventos.
- [x] **F2** · Panel flotante, bandeja, atajos, pegado, reposo en RAM y arranque con Windows.
- [x] **F3** · Capturas de pantalla dentro del dictado, editor de sesiones y «Copiar para IA» (chat o agente).
- [x] **F4** · Organizador de tareas por categorías con un LLM local (LM Studio, gpt-oss-20b): bandeja de propuestas,
  ventana Tareas, «Extraer tareas» en el editor y salvaguarda de la GPU.
- [ ] **F5** · Servidor MCP, webhooks y carpeta `.md` para otras herramientas, y limpieza opcional del texto con un LLM.

## Licencia

[MIT](LICENSE). Las dependencias tienen sus propias licencias; en particular PySide6/Qt se usa bajo LGPL-3.0, el
modelo Whisper large-v3 de OpenAI es MIT y gpt-oss-20b (tareas; se descarga aparte en LM Studio) es Apache-2.0
(detalle en [docs/STACK.md](docs/STACK.md)).
