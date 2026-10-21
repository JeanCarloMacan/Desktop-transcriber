# Desarrollo

## Entorno

```powershell
uv sync                                     # .venv con dependencias de ejecución, desarrollo y spikes
.venv\Scripts\voice-core --descargar-modelo # si aún no tienes el modelo
```

- Python 3.13 en Windows. El proyecto se instala en modo editable: `src/voice_core` y `src/overlay`.
- La configuración se busca en `TRT_CONFIG`, luego `config/voice-core.toml` (personal, ignorado por git) y por último
  `config/voice-core.example.toml`.
- Sin GPU NVIDIA todo funciona en CPU, salvo los tests marcados `gpu`.

## Ejecutar

| Para… | Comando |
|---|---|
| Panel + bandeja (lanza el servicio) | `.venv\Scripts\python -m overlay` (con registro en consola) o `pythonw -m overlay` |
| Solo el servicio, con consola | `.venv\Scripts\python -m voice_core` |
| Servicio sin atajos, en otro puerto | `.venv\Scripts\python -m voice_core --sin-hotkeys --puerto 8799` |
| Probar sin micrófono | `.venv\Scripts\python -m voice_core --fuente-archivo ruta\al\audio.wav` |
| Ver el texto en vivo | `.venv\Scripts\voice-core-cli seguir` |
| Estado / liberar / modo | `voice-core-cli estado` · `voice-core-cli liberar` · `voice-core-cli modo cpu` |

## Tests

```powershell
.venv\Scripts\python -m pytest              # unitarios: sin GPU ni micrófono (dobles de prueba en tests/conftest.py)
.venv\Scripts\python -m pytest -m gpu -s    # punta a punta con el modelo real en la GPU permitida
```

| Archivo | Cubre |
|---|---|
| `test_segmentador.py` | frases por pausas, parciales, frases largas, ruidos cortos, alucinaciones |
| `test_motor.py` | elección de dispositivo, búfer de arranque, inactividad, reposo en RAM, cesión de VRAM, fallos del worker |
| `test_api.py` | contrato REST y WebSocket, 409/404/422, `/shutdown`, `stop_session` |
| `test_almacen_y_contratos.py` | esquema SQLite, validación de eventos, **deriva de `contracts/`**, atajos |
| `test_gpu_seleccion.py` | GPU por nombre o UUID; ambigüedad = ninguna |
| `test_overlay_estado.py` | lógica del panel sin Qt |
| `test_overlay_panel.py` | panel Qt en plataforma *offscreen*: tamaño, redimensión, rueda, letra, persistencia |
| `test_cli_vista.py` | visor de consola en párrafo, con un emulador mínimo de terminal |
| `test_gpu_e2e.py` (`-m gpu`) | GPU con motor caliente, arranque en frío, CPU y ciclo de reposo en RAM; necesita `datos/f0/audios/dictado_08.wav` (se omite si no está) |

## Contratos

Los modelos de `src/voice_core/contratos.py` son la fuente de verdad. Tras cambiarlos:

```powershell
.venv\Scripts\python scripts\exportar_contratos.py              # regenera contracts/
.venv\Scripts\python scripts\exportar_contratos.py --comprobar  # lo que comprueba el test de deriva
```

Cambios de esquema de la base de datos: añadir `src/voice_core/migraciones/000N_descripcion.sql`; se aplica sola al
arrancar (`PRAGMA user_version`).

## Convenciones

- **Especificación primero**: cada fase empieza con objetivo, no objetivos, contratos y criterios de aceptación en
  `docs/F<n>_*.md`, y termina con los resultados medidos y lo que se corrigió.
- Identificadores, comentarios y documentación en español; las rutas y campos de la API pública en inglés
  (`/sessions`, `session_id`) y los valores de dominio en español (`dictado`, `en_reposo`).
- La lógica que no necesita E/S va en módulos puros y testeables (`segmentador.py`, `overlay/estado.py`).
- Nada de `print` en el código del servicio: `logging` (los CLI escriben a `stdout` a propósito).
- Todo lo que llega de otros hilos al bucle asyncio entra con `call_soon_threadsafe`; nada de CUDA en el supervisor.
- `datos/` y la configuración personal nunca van a git.

## Spikes

`spikes/` guarda los experimentos que justificaron las decisiones; no forman parte del servicio.

| Script | Qué midió |
|---|---|
| `spikes/f0/bench.py` (+ `bench_motor.py`, `comparar.py`, `grabar.py`) | large-v3 vs turbo, GPU vs CPU, efecto del glosario |
| `spikes/f1/contexto.py` | pausa que cierra frase (700 vs 1000 ms) y texto previo como contexto |
| `spikes/f2/reposo_ram.py` | coste de mover el modelo entre GPU y RAM |

## Capturas para la documentación

```powershell
.venv\Scripts\python scripts\capturar_panel.py   # regenera docs/img/panel.png con el panel real, sin mostrarlo
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
| Registros | — | `datos/overlay.log`, `datos/voice-core.log`, `datos/voice-core.salida.log` |
