# Stack tecnológico

Versiones fijadas en `uv.lock` (las de la tabla son las probadas en la versión 0.2.0).

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
| [nvidia-ml-py](https://pypi.org/project/nvidia-ml-py/) | 13.615 | NVML | leer la VRAM y elegir la GPU **sin crear contexto CUDA** | BSD-3 |
| CUDA Toolkit (cuBLAS) | 12.x (probado 12.9) | DLLs de CUDA | ya instalado en el equipo: evita bajar PyTorch (~4 GB) solo por cuBLAS | EULA de NVIDIA |
| cuDNN 9 | cargador incluido en el wheel de CTranslate2 | — | en el equipo de pruebas no hizo falta instalar nada más | EULA de NVIDIA |
| [huggingface_hub](https://huggingface.co/docs/huggingface_hub) | 2.2.0 | descarga del modelo | `--descargar-modelo`, a carpetas normales (sin symlinks) | Apache-2.0 |
| [PySide6-Essentials](https://doc.qt.io/qtforpython-6/) (Qt 6) | 6.12.0 | panel y bandeja | translucidez real, fuentes nítidas en alta densidad, bandeja nativa | LGPL-3.0 |
| Win32 (ctypes) | — | `RegisterHotKey`, `WS_EX_NOACTIVATE`, `keybd_event`, mutex | atajos globales sin permisos especiales; un panel que no roba el foco | — |

## Desarrollo

| Pieza | Versión | Papel |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 0.12 | entorno, dependencias y bloqueo (`uv.lock`) |
| hatchling | — | empaquetado (`src/voice_core`, `src/overlay`) |
| pytest | 9.1 | tests (80+ unitarios sin GPU, 4 de punta a punta con `-m gpu`) |
| httpx | 0.28 | cliente de pruebas de FastAPI |
| num2words | 0.5 | normalización de cifras para el WER de los spikes |

## Alternativas evaluadas

| Necesidad | Elegido | Descartado y por qué |
|---|---|---|
| Motor de voz | faster-whisper large-v3 | large-v3-turbo (más errores con jerga), Parakeet (sin glosario), servicios en la nube (coste, privacidad), dictado de Windows (no es un servicio) |
| Interfaz | PySide6 | Tauri (añade Rust y TypeScript), Electron (pesado), tkinter (sin translucidez por píxel, alta densidad borrosa) |
| Integración | WebSocket + REST con `seq` | broker NATS/Redis (sobredimensionado), webhooks (quedan para F5) |
| Liberar VRAM | worker en proceso aparte + reposo en RAM | descargar el modelo en el mismo proceso (deja ≈ 240 MB de contexto y no evita el arranque lento) |

## Licencias de terceros

El código del proyecto es [MIT](../LICENSE). PySide6/Qt se usa bajo **LGPL-3.0** como biblioteca enlazada
dinámicamente desde PyPI (sin modificarla); python-soxr es LGPL-2.1+ en las mismas condiciones. El CUDA Toolkit y
cuDNN están sujetos a la licencia de NVIDIA y no se redistribuyen en este repositorio.
