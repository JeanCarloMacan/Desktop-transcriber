# Desarrollo

## Entorno

```powershell
uv sync                                     # .venv con dependencias de ejecución, desarrollo y spikes
.venv\Scripts\voice-core --descargar-modelo # si aún no tienes el modelo
```

- Python 3.13 en Windows. El proyecto se instala en modo editable: `src/voice_core`, `src/overlay` y
  `src/tasks_service`.
- La configuración se busca en `TRT_CONFIG`, luego `config/voice-core.toml` (personal, ignorado por git) y por último
  `config/voice-core.example.toml`. tasks-service lee la misma, sección `[tareas]`.
- Sin GPU NVIDIA todo funciona en CPU, salvo los tests marcados `gpu`. LM Studio solo hace falta para extraer tareas de
  verdad: los tests usan un LLM falso.

## Ejecutar

| Para… | Comando |
|---|---|
| Panel + bandeja (lanza los servicios) | `.venv\Scripts\python -m overlay` (con registro en consola) o `pythonw -m overlay` |
| Solo el servicio, con consola | `.venv\Scripts\python -m voice_core` |
| Servicio sin atajos, en otro puerto | `.venv\Scripts\python -m voice_core --sin-hotkeys --puerto 8799` |
| Probar sin micrófono | `.venv\Scripts\python -m voice_core --fuente-archivo ruta\al\audio.wav` |
| Ver el texto en vivo | `.venv\Scripts\voice-core-cli seguir` |
| Estado / liberar / modo | `voice-core-cli estado` · `voice-core-cli liberar` · `voice-core-cli modo cpu` |
| Solo tasks-service, con consola | `.venv\Scripts\python -m tasks_service` (consume el voice-core de `[servidor]`; `--puerto` y `--config` con otro `[datos] carpeta` para probar sin tocar `datos/tareas.db`) |
| Salud de los servicios | `curl.exe -s http://127.0.0.1:8765/health` · `curl.exe -s http://127.0.0.1:8766/health` (debe decir `"voice_core":true`) · `curl.exe -s http://127.0.0.1:8766/llm` |

**Tras cambiar código**, reinicia todo: `POST /shutdown` a voice-core (`:8765`) **y a tasks-service (`:8766`)**, cierra
el overlay y vuelve a lanzarlo. El overlay solo arranca un servicio si no está ya en marcha: si dejas tasks-service
vivo, el overlay nuevo se conecta al servicio viejo. `uv sync` falla mientras quede algún proceso del `.venv` abierto.

```powershell
curl.exe -s -X POST http://127.0.0.1:8765/shutdown
curl.exe -s -X POST http://127.0.0.1:8766/shutdown
```

## Tests

```powershell
.venv\Scripts\python -m pytest              # 146 unitarios, sin GPU, micrófono ni LM Studio (dobles en conftest.py)
.venv\Scripts\python -m pytest -m gpu -s    # 4 de punta a punta con el modelo real en la GPU permitida
```

Los tests marcados `gpu` se deseleccionan por defecto (`addopts` en `pyproject.toml`).

| Archivo | Cubre |
|---|---|
| `test_segmentador.py` | frases por pausas, parciales, frases largas, ruidos cortos, alucinaciones, eco del glosario |
| `test_motor.py` | elección de dispositivo, búfer de arranque, inactividad, reposo en RAM, cesión de VRAM, fallos del worker |
| `test_api.py` | contrato REST y WebSocket, 409/404/422, `/shutdown`, `stop_session`, `session.stopped` |
| `test_api_documento.py` | capturas (durante y después del dictado, 413/415, descarga) y documento (editar, volver al original, 409 mientras graba, eventos) |
| `test_documento.py` | lógica pura del documento: imágenes por tiempo, párrafos re-unidos, exportar para chat, agente y texto |
| `test_almacen_y_contratos.py` | esquema SQLite, migración de una base de F1, validación de eventos, **deriva de `contracts/`** (voz y tareas), atajos |
| `test_gpu_seleccion.py` | GPU por nombre o UUID; ambigüedad = ninguna |
| `test_overlay_estado.py` | lógica del panel sin Qt, con las marcas de captura y la parada mientras carga el modelo |
| `test_overlay_panel.py` | panel Qt en plataforma *offscreen*: tamaño, redimensión, rueda, letra, persistencia |
| `test_overlay_editor.py` | editor *offscreen* (guardar, copiar para chat y agente, insertar, escalar imágenes) y capturador con el portapapeles de Qt |
| `test_tareas_parser.py` | lógica pura del organizador: categorías (con «Otros»), JSON Schema, prompt con fecha y calendario, `interpretar` (limpia, valida, quita repetidas) |
| `test_tareas_servicio.py` | tasks-service con LLM y voice-core falsos: extracciones y propuestas (repetir sustituye), errores, aprobar y editar, eventos, consumidor (una vez por dictado, retomar al arrancar), salvaguarda de la GPU y descarga del modelo |
| `test_tareas_gpu.py` | cuándo la GPU de LM Studio cuenta como ocupada (VRAM, cómputo sostenido, picos sueltos), con NVML falso |
| `test_overlay_tareas.py` | ventana Tareas *offscreen*: vistas y recuentos, aprobar, eventos, marcar hecha, añadir, y «Extraer tareas» desde el editor |
| `test_cli_vista.py` | visor de consola en párrafo, con un emulador mínimo de terminal |
| `test_gpu_e2e.py` (`-m gpu`) | GPU con motor caliente, arranque en frío, CPU y ciclo de reposo en RAM; necesita `datos/f0/audios/dictado_08.wav` (se omite si no está) |

## Contratos

Los modelos de `src/voice_core/contratos.py` (voz) y `src/tasks_service/contratos.py` (tareas) son la fuente de
verdad. Tras cambiarlos:

```powershell
.venv\Scripts\python scripts\exportar_contratos.py              # regenera contracts/ (voz y tareas)
.venv\Scripts\python scripts\exportar_contratos.py --comprobar  # lo que comprueba el test de deriva
```

Cambios de esquema de la base de datos: añadir `src/voice_core/migraciones/000N_descripcion.sql` (o
`src/tasks_service/migraciones/` para `tareas.db`); se aplica sola al arrancar (`PRAGMA user_version`).

## Convenciones

- **Especificación primero**: cada fase empieza con objetivo, no objetivos, contratos y criterios de aceptación en
  `docs/F<n>_*.md`, y termina con los resultados medidos y lo que se corrigió.
- Identificadores, comentarios y documentación en español; las rutas y campos de la API pública en inglés
  (`/sessions`, `session_id`) y los valores de dominio en español (`dictado`, `en_reposo`).
- La lógica que no necesita E/S va en módulos puros y testeables (`segmentador.py`, `documento.py`,
  `overlay/estado.py`, `tasks_service/parser.py`).
- Nada de `print` en el código de los servicios: `logging` (los CLI escriben a `stdout` a propósito).
- Todo lo que llega de otros hilos al bucle asyncio entra con `call_soon_threadsafe`, y lo bloqueante (LM Studio,
  REST de voice-core, NVML) sale de él con `asyncio.to_thread`; nada de CUDA en el supervisor ni en tasks-service.
- `datos/` y la configuración personal nunca van a git.

## Spikes

`spikes/` guarda los experimentos que justificaron las decisiones; no forman parte del servicio.

| Script | Qué midió |
|---|---|
| `spikes/f0/bench.py` (+ `bench_motor.py`, `comparar.py`, `grabar.py`) | large-v3 vs turbo, GPU vs CPU, efecto del glosario |
| `spikes/f1/contexto.py` | pausa que cierra frase (700 vs 1000 ms) y texto previo como contexto |
| `spikes/f2/reposo_ram.py` | coste de mover el modelo entre GPU y RAM |
| `spikes/f4/banco.py` (+ `dictados.json`) | modelos de LM Studio para extraer tareas: 14 dictados inventados con el mismo prompt y esquema que el servicio; aciertos de tareas, categoría y fecha, carga en frío y tiempo por dictado |

El banco de F4 necesita el servidor de LM Studio en marcha y el modelo descargado; descarga el modelo antes (para
medir la carga en frío) y después, y deja los resultados en `datos/f4/`. Los parámetros extra van con `--param`:

```powershell
.venv\Scripts\python spikes\f4\banco.py openai/gpt-oss-20b --param reasoning_effort=high
```

## Capturas para la documentación

```powershell
.venv\Scripts\python scripts\capturar_panel.py   # regenera docs/img/panel.png, editor.png y tareas.png, sin mostrarlos
```

## Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| «No está el modelo en …» | modelo sin descargar o `carpeta_modelos` distinta | `voice-core --descargar-modelo` o revisar `motor.carpeta_modelos` |
| Siempre en CPU | `motor.gpu` no coincide con ninguna GPU (o con varias) | `nvidia-smi -L` y poner parte del nombre o el UUID |
| `cublas64_12.dll` no encontrada | falta el CUDA Toolkit 12.x | instalarlo o indicar la carpeta en `motor.cuda_dll_dirs` |
| «No se pudo registrar ctrl+alt+…» | el atajo lo usa otra aplicación | cambiarlo en `[hotkeys]` u `[overlay]` |
| `WinError 1314` al descargar modelos | caché de Hugging Face con symlinks sin modo desarrollador | usar `--descargar-modelo` (descarga sin symlinks) |
| El panel no aparece | overlay no arrancado o fuera de pantalla | icono en la bandeja; borrar `datos/overlay.json` |
| `Ctrl+Alt+S` no abre el recorte | la Herramienta Recortes no está instalada o el atajo lo usa otra aplicación | instalarla desde Microsoft Store; cambiar `[overlay] capturar` |
| La captura no llega | se canceló el recorte o pasó más de 1 min | volver a pulsar `Ctrl+Alt+S`; el registro dice «Recorte cancelado» |
| Acentos rotos («nÃºmero») con `Invoke-RestMethod` | Windows PowerShell 5.1 lee el JSON como ISO-8859-1 | PowerShell 7 (`pwsh`), `curl.exe` o Python; el servicio responde en UTF-8 |
| Extracción en error «LM Studio no responde en …» | LM Studio cerrado y `lms server start` no pudo arrancar su servidor (o `arrancar_lmstudio = false`) | abrir LM Studio y arrancar su servidor local; después, «Extraer tareas» en el editor la reintenta. `curl.exe -s http://127.0.0.1:8766/llm` dice si responde |
| LM Studio responde pero el modelo no se carga solo | la carga a demanda (JIT) está desactivada en LM Studio | activarla en los ajustes de su servidor local |
| Error de LM Studio al extraer (el modelo no existe) | modelo no descargado, o `[tareas] modelo` no coincide con el identificador de LM Studio | descargarlo en LM Studio con el identificador de `[tareas] modelo` (`openai/gpt-oss-20b`); `lms ls` lista los descargados |
| Extracción «en espera» (GPU ocupada) | otro programa usa la GPU de LM Studio (más de `gpu_vram_max_mib` o de `gpu_uso_max_pct`) | es lo esperado: se hace sola al liberarse (se mira cada `reintento_gpu_s`); con `[tareas] gpu = ""` no se comprueba |
| Tras cambiar código, el overlay no ve los cambios de tareas | tasks-service viejo sigue vivo y el overlay nuevo se conecta a él | `POST :8766/shutdown` además de `:8765`, y volver a lanzar el overlay |
| No salen «é», «ö» u «ó» con AltGr | AltGr equivale a `Ctrl+Alt`: los atajos `Ctrl+Alt+E`, `P` y `O` los tapan (teclado US-Internacional) | cambiar `[overlay] editor`, `tareas` o `mostrar` |
| Registros | — | `datos/overlay.log`, `datos/voice-core.log`, `datos/tasks-service.log` y los `*.salida.log` de cada servicio |
