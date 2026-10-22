# Stack tecnológico

Versiones fijadas en `uv.lock` (las de la tabla son las probadas en la versión 0.4.0).

## Runtime

| Pieza | Versión | Papel | Por qué | Licencia |
|---|---|---|---|---|
| Python | 3.13 | lenguaje | un solo lenguaje para servicio, motor y panel | PSF |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 1.2.1 | transcripción | Whisper sobre CTranslate2: hasta 4× más rápido que la implementación original, sin PyTorch | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | 4.8.3 | inferencia | cuantización `int8_float16` (mitad de VRAM sin perder velocidad), `unload_model`/`load_model` para el reposo en RAM, funciona en Blackwell (RTX 50) | MIT |
| Whisper `large-v3` ([Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3)) | — | modelo | con glosario: 3,2 % WER y 24/24 términos técnicos en dictados reales; turbo quedó en 9,5 % | MIT (OpenAI) |
| Silero VAD v6 | incluido en faster-whisper | detección de voz | ligero, en CPU vía ONNX, en streaming de 32 ms | MIT |
| [ONNX Runtime](https://onnxruntime.ai/) | 1.31.0 | ejecuta el VAD | dependencia de faster-whisper | MIT |
| [sounddevice](https://python-sounddevice.readthedocs.io/) | 0.5.6 | micrófono | PortAudio (WASAPI/MME) con callback en su propio hilo | MIT |
| [python-soxr](https://github.com/dofuuz/python-soxr) | 1.1.0 | remuestreo a 16 kHz | alta calidad, en streaming, ~1 MB | LGPL-2.1+ |
| [soundfile](https://python-soundfile.readthedocs.io/) | 0.14.0 | WAV | guardar el audio de cada sesión | BSD-3 |
| NumPy | 2.5.4 | audio en memoria | — | BSD-3 |
| [FastAPI](https://fastapi.tiangolo.com/) | 0.159.1 | API REST + WebSocket | contrato tipado con Pydantic y OpenAPI automático | MIT |
| [uvicorn](https://www.uvicorn.org/) | 0.54.0 | servidor ASGI | apagado controlable (`should_exit`, `timeout_graceful_shutdown`) | BSD-3 |
| [Pydantic](https://docs.pydantic.dev/) | 2.14.1 | contratos y configuración | validación de entradas, eventos con discriminador por `type` | MIT |
| [websockets](https://websockets.readthedocs.io/) | 17.3 | WebSocket del servidor y cliente síncrono | — | BSD-3 |
| SQLite (stdlib) | — | sesiones, segmentos, eventos | sin servidor, WAL, migraciones SQL | dominio público |
| [nvidia-ml-py](https://pypi.org/project/nvidia-ml-py/) | 13.615 | NVML | leer la VRAM y elegir la GPU **sin crear contexto CUDA**; en tasks-service, mirar si otro programa usa la GPU de LM Studio (VRAM y cómputo) antes de cargar el modelo | BSD-3 |
| CUDA Toolkit (cuBLAS) | 12.x (probado 12.9) | DLLs de CUDA | ya instalado en el equipo: evita bajar PyTorch (~4 GB) solo por cuBLAS | EULA de NVIDIA |
| cuDNN 9 | cargador incluido en el wheel de CTranslate2 | — | en el equipo de pruebas no hizo falta instalar nada más | EULA de NVIDIA |
| [huggingface_hub](https://huggingface.co/docs/huggingface_hub) | 2.2.0 | descarga del modelo | `--descargar-modelo`, a carpetas normales (sin symlinks) | Apache-2.0 |
| [PySide6-Essentials](https://doc.qt.io/qtforpython-6/) (Qt 6) | 6.12.0 | panel, bandeja y editor | translucidez real, fuentes nítidas en alta densidad, bandeja nativa; `QTextDocument` lee y escribe Markdown con imágenes; arrastre de archivos (`QDrag`) | LGPL-3.0 |
| Win32 (ctypes) | — | `RegisterHotKey`, `WS_EX_NOACTIVATE`, `keybd_event`, mutex | atajos globales sin permisos especiales; un panel que no roba el foco | — |
| Recorte de Windows (Herramienta Recortes) | la del sistema | capturas | se abre con el URI `ms-screenclip:` y deja la imagen en el portapapeles; nada que instalar ni mantener | incluida en Windows |
| [LM Studio](https://lmstudio.ai/) | aplicación aparte (opcional) | servidor del LLM de tareas | servidor local compatible con OpenAI en `127.0.0.1:1234` que impone la salida con JSON Schema; carga el modelo a demanda (JIT) con `ttl` y su API REST v1 permite descargarlo al vaciarse la cola; elige la GPU por su cuenta, así que tasks-service nunca toca CUDA; cliente con `urllib` de la biblioteca estándar, sin SDK | sus propias condiciones de uso; no se redistribuye |
| gpt-oss-20b ([openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b)) | — (MXFP4) | extracción de tareas | con `reasoning_effort=high`: 14/14 dictados del banco de F4, mediana ≈ 4,3 s, ≈ 12 GB de VRAM; Qwen3.5-35B-A3B también hizo 14/14, pero con ≈ 12,6 s y 22 GB | Apache-2.0 (OpenAI) |

## Desarrollo

| Pieza | Versión | Papel |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 0.12 | entorno, dependencias y bloqueo (`uv.lock`) |
| hatchling | — | empaquetado (`src/voice_core`, `src/overlay`, `src/tasks_service`) |
| pytest | 9.1 | tests (146 unitarios sin GPU ni LM Studio; 4 de punta a punta marcados `gpu`, que se deseleccionan por defecto y se corren con `-m gpu`) |
| httpx | 0.28 | cliente de pruebas de FastAPI |
| num2words | 0.5 | normalización de cifras para el WER de los spikes |

## Alternativas evaluadas

| Necesidad | Elegido | Descartado y por qué |
|---|---|---|
| Motor de voz | faster-whisper large-v3 | large-v3-turbo (más errores con jerga), Parakeet (sin glosario), servicios en la nube (coste, privacidad), dictado de Windows (no es un servicio) |
| Interfaz | PySide6 | Tauri (añade Rust y TypeScript), Electron (pesado), tkinter (sin translucidez por píxel, alta densidad borrosa) |
| Integración | WebSocket + REST con `seq` | broker NATS/Redis (sobredimensionado), webhooks (quedan para F5) |
| Liberar VRAM | worker en proceso aparte + reposo en RAM | descargar el modelo en el mismo proceso (deja ≈ 240 MB de contexto y no evita el arranque lento) |
| Capturas | recorte de Windows por `ms-screenclip:` | herramienta de recorte propia (más código; la de Windows ya permite anotar antes de copiar) |
| LLM de tareas | gpt-oss-20b con `reasoning_effort=high` en LM Studio ([banco de F4](F4_TAREAS.md#prueba-de-modelos)) | Qwen3.5-35B-A3B (igual de preciso, pero ≈ 3× más lento y casi el doble de memoria; LM Studio ignora `enable_thinking: false`), gpt-oss-20b con razonamiento por defecto o `medium` (falla fechas relativas) |
| Revisión de tareas | bandeja de propuestas que apruebas | que el LLM escriba directamente en la lista (sus errores la ensuciarían) |
| GPU ocupada al extraer | esperar y reintentar solo (NVML cada 60 s) y descargar el modelo al vaciarse la cola | cargar siempre; dejar el modelo cargado con un TTL largo (600 s) |

## Licencias de terceros

El código del proyecto es [MIT](../LICENSE). PySide6/Qt se usa bajo **LGPL-3.0** como biblioteca enlazada
dinámicamente desde PyPI (sin modificarla); python-soxr es LGPL-2.1+ en las mismas condiciones. El CUDA Toolkit y
cuDNN están sujetos a la licencia de NVIDIA y no se redistribuyen en este repositorio. LM Studio es una aplicación
aparte con sus propias condiciones y el modelo gpt-oss-20b (**Apache-2.0**) se descarga en ella: ninguno de los dos se
incluye en el repositorio; tasks-service solo habla con LM Studio por HTTP local.
