# Pendientes

Ordenados por prioridad. Al terminar uno, se borra de aquí (y queda en la bitácora).

## 1. Al empezar la próxima sesión

- [ ] **Usuario:** commit y push de F4 (v0.4.0) con los comandos que se le dieron al cerrar la sesión 2, si no lo
      hizo ya (`git log -1`).
- [ ] **Usuario:** confirmar de pasada lo último que se desplegó y no se vio en vivo: las **ayudas de los iconos del
      panel** salen al pasar el ratón (y dicen su atajo) y **`Ctrl+Alt+E`** abre el editor en el último dictado y lo
      oculta si ya está delante. Si algo falla: `datos/overlay.log`.

## 2. F5 (siguiente fase)

Del plan original ([plan-transcriptor-rt.svg](../planes/plan-transcriptor-rt.svg)): servidor **MCP** para que Claude
Code, Claude Desktop o LM Studio pidan «mi último dictado con sus capturas» o las tareas; **webhooks** y carpeta `.md`
para n8n, Obsidian o scripts; glosario editable; **limpieza opcional del texto con un LLM**. Empezar, como siempre,
preguntando al usuario qué quiere primero.

Si F5 usa un LLM (limpieza del texto), reutilizar la **salvaguarda de la GPU** de tasks-service (`VigiaGpu`, D22) y
la descarga al terminar (D23): el usuario no quiere modelos ocupando la 5090 cuando otros servicios suyos la usan.

## 3. Ideas y deuda menor (sin compromiso)

- Prioridad, subtareas o recordatorios por fecha (fuera de alcance de F4 a propósito).
- `GET /tasks?status=hecha` devuelve como mucho 200 en la ventana; paginar si crece.
- Extraer de un dictado de *feedback* puede proponer «tareas» discutibles: es esperable (para eso está la bandeja).
- La primera carga de Whisper tras reiniciar la bandeja tardó una vez 23 s (lo normal, ≈ 5 s); no se repitió. Si vuelve
  a pasar, medir si es la caché de disco del sistema (tras cargar los 12 GB de gpt-oss) o el arranque de CUDA.
- La ventana Tareas no muestra las propuestas sustituidas (quedan `descartada`); si el usuario quiere ver el historial
  de extracciones, añadir una vista.
- Hay una carpeta vacía que dejó el borrado de large-v3-turbo en la caché de modelos; se ofreció borrarla y el usuario
  no respondió.
