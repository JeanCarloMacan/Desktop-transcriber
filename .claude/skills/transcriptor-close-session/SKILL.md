---
name: "transcriptor-close-session"
description: "Cierra una sesión del proyecto Transcriptor RT: comprueba tests, contratos y anonimización, actualiza docs/contexto (bitácora, estado, pendientes, decisiones y aprendizajes), da al usuario los comandos de commit y push y deja el mensaje exacto para abrir la siguiente sesión. Úsala cuando el usuario diga «cerremos», «terminamos por hoy», «guarda dónde nos quedamos», «deja el prompt para mañana», «actualiza la base de conocimiento» o al terminar una fase, aunque no la nombre."
---

# Cierre de sesión — Transcriptor RT

Objetivo: que la próxima sesión (o alguien sin memoria) pueda continuar leyendo solo `docs/contexto/ESTADO.md` y
`PENDIENTES.md`. Nada debe quedar solo en el chat. Escribe para alguien que no vio esta sesión: hechos, números y
rutas, no impresiones.

## 1. Reunir los hechos (antes de escribir nada)

- Fecha real: `Get-Date -Format yyyy-MM-dd`.
- `git status --short` y `git log -3 --oneline`: qué cambió y qué está ya subido.
- Si se tocó código: `.venv\Scripts\python -m pytest -q` y
  `.venv\Scripts\python scripts\exportar_contratos.py --comprobar`. Anota el número real de tests; si algo falla,
  dilo tal cual (va a la bitácora y a PENDIENTES), nunca lo des por bueno sin haberlo ejecutado.
- Repasa la conversación: qué pidió y decidió el usuario, qué se probó, qué falló y cómo se arregló, qué números se
  midieron (tiempos, VRAM, aciertos).

## 2. Bitácora — `docs/contexto/BITACORA.md`

Completa la entrada de hoy (la abrió la skill de inicio, **arriba**, marcada «(en curso)»); si no existe, créala
arriba con `## YYYY-MM-DD · Sesión N · <resumen en una línea>`. Quita «(en curso)» y usa estas secciones, omitiendo
las vacías:

- **Hecho**: qué se construyó o cambió (módulos, archivos, docs).
- **Problemas y soluciones**: síntoma exacto → causa → arreglo. Es lo más valioso para el futuro.
- **Resultados medibles**: tests, tiempos, VRAM, aciertos, lo que se haya medido.
- **Decisiones**: una línea cada una, con su número `D<nn>` de DECISIONES.
- **Pendiente al cierre**: viñetas con quién lo hace (usuario / Claude).

## 3. Estado — `docs/contexto/ESTADO.md`

**Reescríbelo, no acumules**: es una foto del ahora y debe leerse en un par de minutos. Mantén sus secciones: En una
línea · Fases (tabla con ✅ / 🟡 / ⬜) · Repositorio (último commit subido, qué queda sin commit, versión en
`pyproject.toml`, nº de tests) · Qué está corriendo en el equipo · Dónde nos quedamos exactamente · Cómo retomar ·
Prompt para abrir la siguiente sesión. Actualiza la fecha de «Última actualización».

## 4. Pendientes, decisiones y aprendizajes

- `PENDIENTES.md`: borra lo terminado (ya queda en la bitácora), añade lo nuevo y deja arriba lo próximo. Si hay una
  fase a medio cerrar, conserva su lista de cierre con casillas.
- `DECISIONES.md`: una fila nueva por decisión de arquitectura, alcance o método (`D<siguiente>`, fecha, decisión,
  descartado, por qué, enlace). Si una decisión cambia otra, añade la fila nueva y menciona a cuál sustituye; no
  borres la vieja.
- `APRENDIZAJES.md`: cada trampa nueva en su grupo, con la forma «qué pasó → regla práctica». Solo lo que pueda volver
  a ocurrir; lo anecdótico se queda en la bitácora.
- Si se cerró una fase: comprueba que se hizo su lista de cierre (diario `docs/F<n>_*.md`, README, API,
  ARQUITECTURA, DESARROLLO, STACK, CHANGELOG, versión en `pyproject.toml` y `src/voice_core/__init__.py`, `uv lock`,
  contratos). Lo que falte va a PENDIENTES.
- Si cambió algo del mapa del código, un puerto, un comando o una regla: actualiza `CLAUDE.md` (que siga siendo
  corto: se carga en cada sesión).
- Si cambiaron estas skills (`.claude/skills/transcriptor-*`), revisa que las dos sigan siendo coherentes entre sí.

## 5. Anonimización (el repo es público)

Antes de proponer el commit, busca datos personales en lo que se va a subir:

```bash
git add --dry-run . | sed "s/^add '//; s/'$//" | grep -v '\.png$' | xargs -d '\n' grep -n -i -E 'GPU-[0-9a-f]{8}-|[\\/]Users[\\/]' 2>/dev/null
```

Las únicas coincidencias esperables son este mismo comando y la regla de `CLAUDE.md`. (El patrón no lleva `C:` a
propósito: Git Bash reescribe los argumentos con `:` y `/` como si fueran rutas y la expresión deja de funcionar.)
Revisa también, a ojo, que no aparezcan identificadores de sesiones reales, nombres de los audios del usuario ni
rutas o nombres de otros proyectos suyos. `config/voice-core.toml`, `config/glosario.txt` y `datos/` nunca se suben
(están en `.gitignore`).

## 6. Commit (lo hace el usuario)

No hagas commit ni push. Da los comandos para PowerShell desde la raíz del repo, uno por bloque:
`git add -A` · `git commit -m "<mensaje>"` · `git push`. Mensaje: si se cerró una fase, como los anteriores
(«F4: organizador de tareas con LM Studio (v0.4.0)»); si no, `sesión YYYY-MM-DD: <resumen de una línea>`. Si la fase
sigue a medias y el usuario prefiere no subir trabajo sin probar, basta con decir qué queda sin commit (ya figura en
ESTADO).

## 7. Resumen final al usuario (≤ 10 líneas)

Hecho · Pendiente · Primer paso de la próxima sesión · Qué archivos de `docs/contexto/` se actualizaron · **Mensaje
exacto para abrir la próxima sesión**, con esta forma (y cópialo también al final de ESTADO.md):

`/transcriptor-start-session — Hoy: <lo que el usuario quedó en hacer antes, si algo>. Seguimos con <primer paso de PENDIENTES>.`
