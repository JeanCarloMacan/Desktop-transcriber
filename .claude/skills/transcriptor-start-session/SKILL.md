---
name: "transcriptor-start-session"
description: "Abre una sesión de trabajo del proyecto Transcriptor RT (Desktop-transcriber: dictado local en tiempo real, panel flotante, editor con capturas y organizador de tareas con LM Studio): carga solo docs/contexto (estado, pendientes, última entrada de la bitácora y títulos de decisiones y aprendizajes), comprueba git y los servicios locales, y propone el objetivo del día. Úsala al empezar cualquier sesión en este repo y siempre que el usuario diga «retomamos», «seguimos», «continuemos con F4/F5», «¿dónde nos quedamos?» o pegue el prompt de inicio, aunque no la nombre."
---

# Inicio de sesión — Transcriptor RT

Objetivo: tener el contexto completo en un par de minutos y con pocos tokens. Todo lo que importa de las sesiones
anteriores está en `docs/contexto/`; `CLAUDE.md` (reglas, comandos y mapa del código) ya se cargó solo. **No leas
código, tests ni diarios de fase (`docs/F<n>_*.md`) hasta acordar el objetivo**: casi nunca hace falta y es lo que más
contexto gasta.

## 1. Leer, en este orden y nada más

1. `docs/contexto/ESTADO.md`: foto del proyecto, qué corre en el equipo y dónde nos quedamos exactamente.
2. `docs/contexto/PENDIENTES.md`: qué sigue, con la lista de verificación de la fase en curso.
3. La entrada más reciente de `docs/contexto/BITACORA.md` (está **arriba**: basta con las primeras ~40 líneas).
4. Solo los títulos de las decisiones, para no contradecir ninguna:
   `Select-String -Path docs/contexto/DECISIONES.md -Pattern '^\| D\d' | ForEach-Object { $_.Line.Substring(0, [Math]::Min(110, $_.Line.Length)) }`
5. Solo los encabezados de `docs/contexto/APRENDIZAJES.md` (`## …`). Si el objetivo del día toca un área (GPU y
   Whisper, Qt, LM Studio, servicios…), lee entera esa sección **antes de proponer cambios**: ahí están las trampas que
   ya costaron tiempo.

## 2. Comprobaciones rápidas

- Fecha real del sistema (`Get-Date -Format yyyy-MM-dd`); nunca la supongas.
- `git status --short` y `git log -1 --oneline`. Compara con la sección «Repositorio» de ESTADO: si hay cambios sin
  commit que ESTADO no menciona, avisa en una línea (quizá el usuario tocó algo).
- Servicios, solo leyendo (con `curl.exe`, no `Invoke-RestMethod`, que estropea los acentos en PowerShell 5.1):
  `curl.exe -s http://127.0.0.1:8765/health` (voice-core) · `curl.exe -s http://127.0.0.1:8766/health` (tasks-service;
  debe decir `"voice_core":true`) · `curl.exe -s http://127.0.0.1:1234/v1/models` (LM Studio). Si algo no responde y el
  objetivo del día lo necesita, propón reiniciar la bandeja con el procedimiento de `CLAUDE.md`; no lo hagas sin
  acordarlo, porque el usuario puede estar dictando.
- No ejecutes `lms` (ni siquiera `lms ls`) solo para mirar: despierta el servidor de LM Studio.

## 3. Presentar y acordar (≤ 12 líneas)

- Estado en 3–5 viñetas (de ESTADO).
- Qué propone PENDIENTES para hoy y qué depende del usuario (una prueba en vivo con su micrófono, una decisión).
- Si el objetivo toca un área con aprendizajes, menciona en una línea los 1–2 que más pesan hoy.
- Una sola pregunta: «¿Vamos con esto o cambiamos el objetivo?». Espera la respuesta antes de cambiar nada.
- Si el mensaje del usuario ya trae el objetivo o el resultado de una prueba («ya probé F4 y…»), no preguntes: confirma
  en una línea y sigue.
- Si el objetivo es una **prueba en vivo** de una fase con interfaz, ofrece guiarla paso a paso con sus criterios de
  aceptación (la skill `piloto-guiado` sirve para eso, si el usuario la quiere).

## 4. Abrir la entrada de bitácora

Cuando el objetivo esté acordado, añade **arriba** en `docs/contexto/BITACORA.md` (debajo del texto de introducción):

```markdown
## YYYY-MM-DD · Sesión N · <objetivo en una línea>

(en curso) Objetivo: <qué se acordó>.
```

`N` = número de la última sesión + 1. La skill de cierre la completa; así, aunque la sesión se corte o se compacte el
contexto, queda constancia de qué se intentaba.

## Reglas del proyecto (detalle en `CLAUDE.md`)

- voice-core: solo la RTX 5080 y solo con VRAM libre; **nunca la RTX 5090**. No tocar la configuración de LM Studio.
- Fase a fase: preguntas → especificación en `docs/F<n>_*.md` → código y tests → reiniciar la bandeja → **prueba en
  vivo del usuario** → solo entonces documentar, CHANGELOG y versión.
- Repo público: nada personal en archivos versionados. El commit y el push los hace el usuario.
- Cerrar siempre con `transcriptor-close-session`.
