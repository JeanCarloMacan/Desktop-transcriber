# Decisiones

Registro breve de las decisiones que dan forma al proyecto. El detalle y las mediciones están en el diario de cada
fase. Para cambiar una, añadir una fila nueva que la sustituya (no borrar la vieja).

| # | Fecha | Decisión | Descartado | Por qué | Detalle |
|---|---|---|---|---|---|
| D01 | 2026-10-21 | Ruta A: todo Python y todo local | Tauri (UI en Rust/TS), voz en la nube | un lenguaje, privado, sin coste, lo más rápido de construir | [plan](../planes/plan-transcriptor-rt.svg) |
| D02 | 2026-10-21 | Voz: faster-whisper `large-v3` `int8_float16` + glosario como `initial_prompt` | turbo, Parakeet, nube | 3,2 % WER y 24/24 términos frente a 9,5 % de turbo | [F0](../F0_SPIKE.md) |
| D03 | 2026-10-21 | Entorno propio con uv, sin PyTorch; cuBLAS del CUDA Toolkit; reutilizar modelos ya descargados | reutilizar el venv de otro proyecto | 5 GB menos, sin acoplarse; poco disco libre | [F0](../F0_SPIKE.md) |
| D04 | 2026-10-21 | Solo la 5080 (por UUID) y solo con ≥ 3 GB libres; si no, CPU. Nunca la 5090 | elegir GPU por índice | decisión del usuario; UUID porque el índice depende del orden de los dispositivos | [ARQUITECTURA](../ARQUITECTURA.md#política-de-gpu) |
| D05 | 2026-10-21 | Supervisor sin CUDA + worker en proceso hijo (`spawn`) | todo en un proceso | liberar la VRAM del todo exige cerrar el proceso | [F1](../F1_VOICE_CORE.md) |
| D06 | 2026-10-21 | Pausa de 1000 ms para cerrar frase; sin texto previo como contexto | 700 ms; contexto entre frases | menos frases partidas; el contexto hacía inventar y repetir | [F1](../F1_VOICE_CORE.md) |
| D07 | 2026-10-21 | Tras dictar: 2 min en GPU → 30 min en RAM → apagado; `Ctrl+Alt+L` libera ya | apagar a los 2 min | arranques de 5–11 s eran molestos; en RAM despierta en 0,37 s | [F2](../F2_OVERLAY.md) |
| D08 | 2026-10-21 | Eventos con `seq` y recuperación con `?after=`; REST + WebSocket | broker NATS/Redis, webhooks | suficiente y sin piezas extra; webhooks quedan para F5 | [ARQUITECTURA](../ARQUITECTURA.md) |
| D09 | 2026-10-21 | Panel PySide6 que no roba el foco; aparece al dictar, se va a los 15 s; copia al terminar | Tauri, Electron, tkinter | translucidez y nitidez reales sin otro lenguaje | [F2](../F2_OVERLAY.md) |
| D10 | 2026-10-21 | Atajos: D dictar, T tarea, L liberar, O panel, V pegar, S capturar (todos con Ctrl+Alt) | Ctrl+Alt+Space | ese ya lo usa otra aplicación del usuario | [F2](../F2_OVERLAY.md) |
| D11 | 2026-10-21 | Arranque con Windows desde la carpeta Inicio; servicios ocultos (`python` + `CREATE_NO_WINDOW`) | — | lo pidió el usuario: con Windows, en la bandeja y sin consolas; se quita con `instalar_inicio.ps1 -Quitar` | [F2](../F2_OVERLAY.md) |
| D12 | 2026-10-21 | Repo público MIT, anonimizado; docs en español con resumen en inglés; el usuario hace commit y push | — | decisión del usuario | [README](../../README.md) |
| D13 | 2026-10-21 | Capturas con el recorte de Windows (`ms-screenclip:`) atadas al instante del dictado | herramienta de recorte propia | nada que mantener; permite anotar antes de copiar | [F3](../F3_EDITOR.md) |
| D14 | 2026-10-21 | Documento Markdown con imágenes tras la frase en curso; editor Qt propio | HTML; imágenes al final; editor web | lo leen los agentes y se pega legible en un chat | [F3](../F3_EDITOR.md) |
| D15 | 2026-10-21 | Dos salidas para la IA: chat (`[Imagen N]` + arrastrar) y agente (rutas absolutas + `documento.md`) | solo una | el usuario usa ambos tipos de herramienta | [F3](../F3_EDITOR.md) |
| D16 | 2026-10-21 | Tareas en un proceso aparte (tasks-service `:8766`, `tareas.db`), consumidor de voice-core por WS | dentro de voice-core | voice-core queda solo para voz; otros servicios podrán usar las tareas | [F4](../F4_TAREAS.md) |
| D17 | 2026-10-21 | Origen de tareas: `Ctrl+Alt+T` automático + «Extraer tareas» en el editor | el LLM revisa todo; frase de voz | sin falsos disparos ni GPU en cada dictado | [F4](../F4_TAREAS.md) |
| D18 | 2026-10-21 | Bandeja de propuestas: el LLM nunca escribe en la lista | directo a la lista | los errores del modelo no ensucian la lista | [F4](../F4_TAREAS.md) |
| D19 | 2026-10-21 | Modelo: `openai/gpt-oss-20b` con `reasoning_effort=high`, JSON Schema estricto y calendario de 21 días en el prompt | Qwen3.5-35B-A3B (igual de preciso, 3× más lento, 22 GB) | 14/14 en el banco, ≈ 4 s, 12 GB | [F4](../F4_TAREAS.md#prueba-de-modelos) |
| D20 | 2026-10-21 | LM Studio con carga a demanda y TTL 600 s; en la 5090 (la 5080 está desactivada en LM Studio) | dejar el modelo cargado | ligero: la memoria solo se ocupa mientras se usa | [F4](../F4_TAREAS.md) |
| D21 | 2026-10-21 | Categorías en `[tareas.categorias]` con descripción; «Otros» siempre | categorías que invente el modelo | orden y control del usuario | [F4](../F4_TAREAS.md#categorías) |
| D22 | 2026-10-22 | Salvaguarda de la GPU de LM Studio: antes de cargar el modelo, 3 lecturas NVML de `[tareas] gpu`; ocupada con > 1 GB usado o > 10 % de cómputo medio → la extracción espera en cola (`waiting`) y se reintenta cada 60 s | cargar siempre; error y reintento a mano; «ocupada» solo si no cabe | el usuario tiene otros servicios que usan la 5090 y no deben competir | [F4](../F4_TAREAS.md#salvaguarda-de-la-gpu) |
| D23 | 2026-10-22 | El modelo se descarga en cuanto se vacía la cola (`POST /api/v1/models/unload`), solo si lo cargó tasks-service; TTL 120 s como red de seguridad. Sustituye el TTL de 600 s de D20 | TTL de 600 s; TTL de 60 s | liberar la 5090 enseguida; cuesta ≈ 5–9 s de carga por extracción | [F4](../F4_TAREAS.md#salvaguarda-de-la-gpu) |
| D24 | 2026-10-22 | Atajos `Ctrl+Alt+P` (ventana Tareas) y `Ctrl+Alt+E` (editor), que alternan mostrar/ocultar. Amplía D10 | solo botones del panel y menú | el usuario quiere abrirlos sin pasar por el panel; con teclado US-Internacional tapan AltGr+P/E (ö, é), como ya pasaba con O | [F4](../F4_TAREAS.md) |
| D25 | 2026-10-22 | Evento `session.stopped` al cerrar el micrófono; parar mientras carga el modelo conserva lo grabado y el panel lo explica | descartar lo grabado al parar durante la carga | no perder lo dictado; el panel parecía colgado | [F4](../F4_TAREAS.md) |
