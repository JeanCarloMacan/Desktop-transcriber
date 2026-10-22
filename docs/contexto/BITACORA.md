# Bitácora

Una entrada por sesión, **la más reciente arriba**. Qué se hizo, qué se decidió y con qué se quedó.

## 2026-10-22 · Sesión 2 · revisión en vivo y cierre de F4 (v0.4.0)

Objetivo: guiar la prueba en vivo de F4 y los `[~]` de F2/F3 y, si salía bien, cerrar F4. Se cumplió, con una ronda de
cambios pedida por el usuario durante la prueba (salvaguarda de la 5090 y ajustes del overlay).

- **Hecho.**
  - Prueba guiada en 7 pasos: criterios 1–10 de F4 confirmados en vivo (salvo el caso de error del 6, cubierto por
    tests).
  - Confirmados también los pendientes de F2 (n.º 5 y n.º 7) y de F3 (n.º 2).
  - Nuevo `src/tasks_service/gpu.py` (`VigiaGpu`, `motivo_ocupada`) y `Nvml.lectura` en `voice_core/gpu.py`.
  - Espera en cola con `waiting` (migración `0002_espera_gpu.sql`) y `ClienteLlm.descargar` (API REST v1).
  - Evento `session.stopped` en voice-core y estado «terminando» en el panel.
  - Filtro `eco_del_glosario`.
  - Overlay: atajos `Ctrl+Alt+P` y `Ctrl+Alt+E`, ayudas visibles, resumen al aprobar, ⏻ con plazo largo y
    `Ctrl+Alt+V` hasta 6 s con aviso.
  - Mayúscula inicial en los títulos.
  - Glosario personal con dos términos más.
  - Cierre de F4: `F4_TAREAS.md` (cerrada, prueba real, problemas, cómo usarlo), README, API, ARQUITECTURA,
    DESARROLLO, STACK (estos cinco con dos subagentes), CHANGELOG 0.4.0, versión, `uv lock`, contratos e imágenes.
- **Problemas y soluciones.**
  - «Aprobar todas» parecía borrar una tarea → iban a categorías distintas y el repintado tapaba el aviso → el aviso
    dice adónde fue cada una.
  - Un término de trabajo salía mal escrito → Whisper, no el LLM → glosario.
  - «Términos frecuentes.» en el texto → eco del `initial_prompt` → filtro.
  - 12 GB en la 5090 durante 10 min, con otros servicios del usuario en esa GPU → D22 y D23.
  - Al parar durante una carga de 23 s, el panel decía «Escuchando…» y ⏻ daba «timed out» → `session.stopped`, y ⏻
    espera lo necesario.
  - Las ayudas del panel no salían (ventana nunca activa) → `WA_AlwaysShowToolTips`.
  - `Ctrl+Alt+V` sin pegar al sujetar más de 2 s → 6 s y aviso.
  - El script de capturas, los tests y el banco de F4 (`spikes/f4/dictados.json`) tenían una categoría y dictados que
    dejaban entrever otros proyectos del usuario → temas genéricos (categoría «Docencia»; las cifras del banco son de
    la versión original, anotado en F4). Un id de sesión real en `API.md` → uno inventado.
  - El reinicio de la bandeja no paraba tasks-service → paso añadido en `CLAUDE.md`.
- **Resultados medibles.**
  - Extracción: 23,8 s en frío; 45 s con `lms server start`; 14,6 s tras esperar a la GPU; 8,3 s + 3,8 s con dos
    seguidas.
  - Espera con otro modelo de 21,9 GB en la 5090: siguió sola en menos de 60 s tras expulsarlo.
  - Descarga del modelo en el acto al vaciarse la cola; la 5090, de 12 GB a 10 MiB.
  - Tests: 134 → **146 sin GPU**; contratos sin deriva.
- **Decisiones:** D22 (salvaguarda de GPU), D23 (descarga al vaciarse la cola; sustituye el TTL de 600 s de D20), D24
  (`Ctrl+Alt+P` y `Ctrl+Alt+E`), D25 (`session.stopped`; parar durante la carga conserva lo grabado).
- **Pendiente al cierre.**
  - Usuario: commit y push de F4 (v0.4.0).
  - Usuario: comprobar de pasada que las ayudas del panel salen y que `Ctrl+Alt+E` funciona; se desplegaron al final y
    no se probaron en vivo.
  - Siguiente: F5.

## 2026-10-21 · Sesión 1 · de la idea a F4

Sesión larga (con varias compactaciones de contexto) que llevó el proyecto de cero a F4.

- **Plan.** Petición inicial: dictar ideas y feedback para pasarlos a modelos de IA, como servicio reutilizable, con un
  panel tipo nota adhesiva y un segundo uso: organizar tareas con LM Studio. Se presentaron tres rutas con un SVG
  (`docs/planes/`); el usuario eligió la **A (todo Python, todo local)** y añadió: rápido y ligero, **nunca la 5090**,
  la 5080 solo si hay espacio, apagado por inactividad, entorno propio reutilizando modelos ya descargados.
- **F0 · motor.** Banco con dictados reales del usuario: large-v3 + glosario 3,2 % WER y 24/24 términos técnicos;
  turbo 9,5 %. Elegido large-v3 `int8_float16`. Se borró turbo de la caché para ahorrar disco.
- **F1 · voice-core.** Servicio FastAPI + worker en proceso aparte, SQLite, eventos con `seq`. Pruebas reales con el
  micrófono: la GPU no se liberaba al terminar (→ `Ctrl+Alt+L` y apagado a los 2 min), frases partidas en pausas para
  pensar (→ 1000 ms), el visor se caía al reiniciar el servicio (→ reconexión).
- **F2 · panel.** PySide6 sin robar el foco, bandeja, `Ctrl+Alt+V/O`, arranque con Windows. Tres rondas de prueba:
  arranque lento (→ reposo en RAM 30 min, despierta en 0,37 s), recuadro pequeño (→ 680 px, 13 pt) y solo crecía en
  horizontal (→ redimensionable en todas direcciones, rueda que no arrastra).
- **Publicación.** Repo público `Desktop-transcriber` (MIT), anonimizado; docs en español con resumen en inglés. El
  usuario hizo el commit y el push de 0.2.0.
- **F3 · editor y capturas.** Decisiones: recorte de Windows, ventana Qt propia, salidas para chat y para agente.
  Probada en vivo: todo funcionó. Documentada y publicada como 0.3.0 (commit `73f79de`, subido por el usuario).
- **F4 · tareas.** Decisiones: `Ctrl+Alt+T` automático + botón en el editor, bandeja de propuestas, ventana Qt, modelo
  elegido con un banco. Banco de 14 dictados: **gpt-oss-20b con `reasoning_effort=high`** 14/14 en ≈ 4 s y 12 GB;
  Qwen3.5-35B-A3B 14/14 pero ≈ 13 s y 22 GB. Implementado tasks-service (`:8766`), ventana Tareas, botón del editor;
  134 tests; prueba de punta a punta con LM Studio real; bandeja reiniciada con F4.
- **Cierre.** Se creó esta base de conocimiento (`CLAUDE.md` + `docs/contexto/`). **Queda pendiente la revisión en
  vivo de F4.**
