# Transcriptor RT (Desktop-transcriber) · guía para Claude

Dictado local en tiempo real para Windows (faster-whisper en la GPU) con panel flotante, editor con capturas y un
organizador de tareas con LM Studio. Todo Python y todo local. Documentación en español.

## Al empezar una sesión

Usa la skill **`/transcriptor-start-session`**: lee **`docs/contexto/ESTADO.md`** (dónde nos quedamos) y
**`PENDIENTES.md`** (qué sigue), comprueba git y los servicios y propone el objetivo. Con eso basta para retomar: no
recorras el código ni los diarios de fase salvo para la tarea concreta. Para algo puntual: `docs/contexto/DECISIONES.md`
(por qué es así), `docs/contexto/APRENDIZAJES.md` (trampas ya pisadas), `docs/F<n>_*.md` (especificación y resultados
de cada fase), `docs/ARQUITECTURA.md`, `docs/API.md`.

## Al cerrar una sesión

Usa la skill **`/transcriptor-close-session`**: actualiza `ESTADO.md` y `PENDIENTES.md`, completa la entrada de hoy
**arriba** en `BITACORA.md`, añade lo que toque en `DECISIONES.md` y `APRENDIZAJES.md`, revisa la anonimización, da los
comandos de commit y deja el mensaje para la siguiente sesión.

## Reglas que no se negocian

- **GPU**: voice-core usa **solo la RTX 5080** y solo si tiene VRAM libre (si no, CPU); **nunca la RTX 5090**. La GPU se
  fija por UUID en `config/voice-core.toml` (personal). LM Studio (tareas) usa la 5090 y tiene la 5080 desactivada en
  su propia configuración: **no tocar la configuración de LM Studio**.
- **Ligero**: los modelos se descargan solos sin uso (voz: 2 min en GPU → 30 min en RAM → apagado). El LLM solo se
  carga si la 5090 está libre (si otro programa la usa, se espera) y se descarga al vaciarse la cola (TTL 120 s de
  reserva).
- **Repo público** (MIT): nada personal en archivos versionados (UUIDs de GPU, rutas `C:\Users\…`, otros proyectos
  del usuario, nombres de sus audios, carpeta de modelos). `config/voice-core.toml`, `config/glosario.txt` y `datos/`
  están en `.gitignore`. Antes de proponer un commit, revisa `git status` y busca esos patrones.
- **El commit y el push los hace el usuario**: solo se le dan los comandos.
- **Fase a fase**: decisiones abiertas con preguntas → especificación en `docs/F<n>_*.md` → código + tests → reiniciar
  la bandeja → **prueba en vivo del usuario** → solo entonces documentar resultados, CHANGELOG y versión.
- Los planes gráficos (SVG) van en `docs/planes/`, no en la raíz.

## Comandos

| Para… | Comando (PowerShell, desde la raíz) |
|---|---|
| Tests (sin GPU ni LM Studio) | `.venv\Scripts\python -m pytest -q` (≈ 3 s; `-m gpu` para los de punta a punta) |
| Contratos | `.venv\Scripts\python scripts\exportar_contratos.py` (`--comprobar` = test de deriva) |
| Imágenes de la doc | `.venv\Scripts\python scripts\capturar_panel.py` (panel, editor y tareas, sin mostrarlos) |
| Salud de los servicios | `curl.exe -s http://127.0.0.1:8765/health` (voz) · `:8766/health` (tareas) · `:1234/v1/models` (LM Studio) |
| Banco de modelos de F4 | `.venv\Scripts\python spikes\f4\banco.py openai/gpt-oss-20b --param reasoning_effort=high` |

**Reiniciar la bandeja** (tras cambiar código): `POST /shutdown` a tasks-service (`:8766`) **y** a voice-core
(`:8765`) —si tasks-service sigue vivo, el overlay nuevo se conecta al viejo—, parar los `pythonw.exe` cuyo
comando contenga `overlay`, `uv sync` (falla si hay procesos del `.venv` vivos) y lanzar
`$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Transcriptor RT.lnk`. El overlay arranca voice-core y
tasks-service ocultos. Registros en `datos/overlay.log`, `datos/voice-core.log` y `datos/tasks-service.log`.

En PowerShell 5.1, `Invoke-RestMethod` estropea los acentos: usa `curl.exe` o Python para leer la API.

## Mapa del código

| Módulo | Qué hace |
|---|---|
| `src/voice_core/` | Servicio de voz `:8765` (FastAPI). `contratos.py` = fuente de verdad de la API y eventos |
| ↳ `motor.py` · `worker.py` | Máquina de estados del motor; proceso hijo con faster-whisper (solo ve la GPU permitida) |
| ↳ `segmentador.py` · `audio.py` | VAD Silero, frases, parciales/finales; micrófono 16 kHz |
| ↳ `sesiones.py` · `documento.py` | Sesiones, capturas y documento Markdown (lógica pura en `documento.py`) |
| ↳ `almacen.py` · `bus.py` | `BaseSqlite` (migraciones + eventos, compartida con tareas) y bus con `seq` |
| ↳ `ajustes.py` | Toda la configuración (`[servidor] [motor] … [overlay] [tareas]`) con valores por defecto |
| `src/tasks_service/` | Organizador de tareas `:8766`: `organizador.py` (consume voice-core por WS, cola, LLM), `parser.py` (prompt + JSON Schema, puro), `llm.py` (LM Studio: completar y descargar), `gpu.py` (salvaguarda: ¿está libre la GPU de LM Studio?, NVML), `voz.py`, `api.py`, `contratos.py` |
| `src/overlay/` | Bandeja y ventanas Qt: `__main__.py` (Controlador), `estado.py` (lógica pura del panel), `panel.py`, `editor.py`, `tareas.py`, `capturas.py`, `cliente.py`, `servicio.py` |
| `contracts/` | OpenAPI y JSON Schema generados (voz y tareas) |
| `.claude/skills/` | Skills del proyecto: `transcriptor-start-session` y `transcriptor-close-session` |
| `spikes/` | Experimentos que justificaron decisiones (F0 motor, F1 pausas, F2 reposo, F4 modelos LLM) |
| `tests/` | `test_*.py` por módulo; dobles en `conftest.py` y en cada test (LLM y voice-core falsos en `test_tareas_servicio.py`) |

## Convenciones

Identificadores, comentarios y docs en español; rutas y campos de la API en inglés (`/sessions`, `session_id`);
valores de dominio en español (`dictado`, `propuesta`). La lógica sin E/S va en módulos puros y testeables. Nada de
`print` en servicios (logging). Lo que llega de otros hilos al bucle asyncio entra con `call_soon_threadsafe` o
`asyncio.to_thread`. Nada de CUDA en el supervisor.
