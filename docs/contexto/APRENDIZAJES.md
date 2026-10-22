# Aprendizajes

Trampas ya pisadas y cómo se resolvieron, para no volver a pagarlas. Agrupadas por tema; añadir al final de cada grupo.

## Windows y herramientas

- **PowerShell 5.1** `Invoke-RestMethod` lee el JSON como ISO-8859-1 y muestra «nÃºmero»: el servicio está bien;
  usar `curl.exe`, Python o PowerShell 7.
- `Get-Content`/`Set-Content` de PowerShell 5.1 estropean los acentos al reescribir archivos: editar con las
  herramientas de edición (UTF-8), nunca con esos cmdlets.
- `uv sync` falla si `pythonw`/`python` del `.venv` están vivos (archivos bloqueados): parar la bandeja antes.
- La instalación es editable (`src/` en el `.pth`): un paquete nuevo en `src/` se importa sin `uv sync`, pero los
  scripts de consola (`tasks-service.exe`) y la versión instalada necesitan `uv sync`.
- La caché de Hugging Face usa symlinks y en Windows sin modo desarrollador da `WinError 1314`: descargar con
  `snapshot_download(local_dir=…)` (`voice-core --descargar-modelo`).
- `Ctrl+Alt+Space` ya lo usa otra aplicación del usuario.
- `pythonw` no tiene stdin/stdout: los servicios se lanzan con `python.exe` + `CREATE_NO_WINDOW` (consola oculta y
  E/S válida).
- El 21 de octubre de 2026 es **miércoles** (cuidado al escribir tests con días de la semana).
- **Git Bash reescribe los argumentos con `:` y `/`** como si fueran rutas (conversión de MSYS): una expresión como
  `'C:/Users/|GPU-…'` deja de coincidir sin dar error. Evitar `:` en el patrón (`[\\/]Users[\\/]`) o usar
  `MSYS_NO_PATHCONV=1`, y probar siempre el patrón con un caso que deba coincidir.
- El usuario usa teclado **US-Internacional**: `Ctrl+Alt+letra` es `AltGr+letra`, así que un atajo global tapa ese
  carácter (E → é, O → ó, P → ö, N → ñ, A → á…). Antes de elegir una letra, mirar `Get-WinUserLanguageList` y avisar.
- `nvidia-smi` en Windows (WDDM) no da la memoria por proceso (`[N/A]`): para saber en qué GPU está un modelo, mirar
  qué proceso aparece en cada una con `--query-compute-apps=gpu_name,pid,process_name` (`llama-server.exe` = LM Studio).

## GPU y Whisper

- `CUDA_VISIBLE_DEVICES=<UUID>` debe fijarse **antes** de importar CTranslate2; el worker comprueba que ve 1 sola GPU.
- Borrar el modelo deja ≈ 240 MB de contexto CUDA: liberar del todo exige cerrar el proceso. `unload_model(to_cpu=True)`
  deja ≈ 250–380 MB y despierta en ≈ 0,4 s.
- La 5080 mueve el escritorio: su VRAM usada oscila ±100 MiB sola. Comprobar liberación por procesos, no por igualdad.
- NVML lee la VRAM sin crear contexto CUDA (el supervisor nunca importa CUDA).
- En CPU, cada llamada a Whisper cuesta 3,5–6 s por el relleno a 30 s: en CPU solo hay texto final.
- Whisper inventa créditos («Subtítulos por…») con audio corto o silencio: parcial mínimo de 0,8 s, filtro por
  `no_speech_prob`/`avg_logprob` y expresión regular de alucinaciones.
- El glosario funciona como **frase natural con tildes**; una lista sin tildes hizo que turbo quitara tildes.
- Whisper puede **escribir el propio glosario** (`initial_prompt`) en una pausa: «Términos frecuentes.» como frase
  suelta. Se filtra con `eco_del_glosario` por segmento de Whisper (no los términos sueltos, que son válidos).
- Un término mal escrito en una tarea suele venir de Whisper (la `quote` ya lo trae mal), no del LLM: se arregla
  añadiéndolo al glosario, no cambiando el modelo de tareas.
- La primera carga de large-v3 tras reiniciar la bandeja tardó una vez **23 s** (lo normal, ≈ 5 s). Mientras carga, el
  audio se guarda y se transcribe al estar listo; la sesión no se cierra hasta entonces (de ahí `session.stopped`).

## Servicios

- uvicorn no termina con un WebSocket abierto: el bus suelta a los suscriptores (cierre 1001) y
  `timeout_graceful_shutdown=3`.
- El consumidor de eventos debe guardar el último `seq` y **solo avanzarlo** (un evento repetido no lo hace retroceder).
- Un consumidor nuevo no debe procesar el historial: se guarda la fecha de instalación y se ignora lo anterior.
- **Reiniciar la bandeja: parar también tasks-service** (`POST :8766/shutdown`). Si sigue vivo, el overlay nuevo «solo
  se conecta», se queda con el código viejo y ya no es su dueño («Salir» no lo cerraría).
- Una petición del overlay que espera a otra cosa (⏻ espera a que se transcriba lo grabado) no puede usar el plazo de
  5 s por defecto: `Cliente.pedir(…, espera_s=…)`.

## Qt (PySide6)

- `QTextDocument.toMarkdown()` corta las líneas a 80 columnas: re-unir párrafos al guardar (`documento.unir_lineas`).
- Insertar una imagen con el cursor en un título hereda el formato de encabezado: insertar al final del bloque, en un
  párrafo nuevo con formatos limpios.
- Fuentes de iconos en **puntos**, no en píxeles (si no, aviso «Point size <= 0»).
- En tests *offscreen* la pantalla mide 800 px de ancho.
- La altura de línea hay que medirla con el texto ya maquetado; el pie oculto no debe contar al medir.
- `Ctrl+V` simulado (pegar) solo después de que el usuario suelte Ctrl y Alt.
- El recorte de Windows se abre con `ms-screenclip:` y deja la imagen en el portapapeles: se espera a
  `QClipboard.dataChanged` con imagen (60 s; si se cancela, el portapapeles no cambia).
- Las **ayudas (tooltips) no salen en una ventana que nunca está activa** (el panel no acepta el foco): hace falta
  `WA_AlwaysShowToolTips`.
- Un aviso que se pone tras una acción lo puede tapar el repintado que provocan los eventos de esa misma acción: los
  mensajes de «lista vacía» solo al cambiar de vista.

## LM Studio y el LLM

- `lms ls` (y otros comandos de `lms`) **despiertan el servidor** de LM Studio en segundo plano.
- La API compatible con OpenAI acepta `response_format` con `json_schema` estricto (con `enum`, `anyOf` y `pattern`)
  y el campo **`ttl`** (descarga el modelo tras ese tiempo sin uso). `GET /api/v0/models` dice si está cargado.
- gpt-oss: el razonamiento llega aparte (`message.reasoning`); con razonamiento bajo falla fechas relativas («el
  sábado» → el de la semana siguiente). Con `reasoning_effort=high` y un calendario en el prompt, acierta.
- Qwen3.5-35B-A3B razona miles de tokens por dictado y LM Studio ignora `chat_template_kwargs.enable_thinking=false`.
- Las fechas de las tareas son días **locales**: convertir `started_at` (UTC) con `astimezone()` antes de dárselas al
  modelo.
- **API REST v1** de LM Studio: `GET /api/v1/models` da `loaded_instances` (con `remaining_ttl_seconds`) y
  `POST /api/v1/models/unload {"instance_id": …}` descarga; no hace falta `lms`. El id de una instancia cargada a
  demanda es la clave del modelo (`openai/gpt-oss-20b`).
- Sin modelo cargado, la VRAM usada de la 5090 oscila entre 10 MiB y ≈ 0,4 GB: el umbral de «ocupada» (1 GB) deja
  margen. Con el modelo cargado por la extracción anterior de la misma cola no se mira la GPU (no reserva más).
- gpt-oss a veces copia el dictado tal cual en el título («revisar el presupuesto…»): el parser pone la inicial en
  mayúscula.
