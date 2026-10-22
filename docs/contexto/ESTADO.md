# Estado del proyecto

Última actualización: **2026-10-22** (fin de la sesión 2). Se reescribe al cerrar cada sesión.

## En una línea

F0–F4 cerradas. **F4 (organizador de tareas con LM Studio) se probó en vivo y se cerró como v0.4.0**, con la
salvaguarda de la 5090 que pidió el usuario durante la prueba. Lo siguiente es **F5**, empezando por preguntar qué se
quiere primero.

## Fases

| Fase | Qué | Estado | Diario |
|---|---|---|---|
| F0 | Elección del motor de voz | ✅ cerrada | [F0_SPIKE.md](../F0_SPIKE.md) |
| F1 | Servicio voice-core (captura, VAD, transcripción, API, eventos) | ✅ cerrada | [F1_VOICE_CORE.md](../F1_VOICE_CORE.md) |
| F2 | Panel flotante, bandeja, atajos, reposo en RAM, arranque con Windows | ✅ cerrada (todo confirmado) | [F2_OVERLAY.md](../F2_OVERLAY.md) |
| F3 | Capturas en el dictado, editor, «Copiar para IA» | ✅ cerrada (todo confirmado) | [F3_EDITOR.md](../F3_EDITOR.md) |
| F4 | tasks-service + ventana Tareas con LM Studio + salvaguarda de GPU | ✅ cerrada (v0.4.0) | [F4_TAREAS.md](../F4_TAREAS.md) |
| F5 | MCP, webhooks, carpeta .md, limpieza con LLM | ⬜ sin empezar | — |

Los `[~]` de F2 (n.º 5 y 7) y F3 (n.º 2) se confirmaron en vivo esta sesión y ya figuran como `[x]` en sus diarios.

## Repositorio

- GitHub: `JeanCarloMacan/Desktop-transcriber` (público, MIT), rama `main`.
- Último commit subido: `73f79de` «F3: capturas en el dictado, editor de sesiones y Copiar para IA (v0.3.0)».
- **Comandos de commit y push de F4 dados al usuario** al cerrar la sesión 2. Si `git log -1` muestra «F4: organizador
  de tareas con LM Studio (v0.4.0)», ya está subido; si no, todo F4 y esta base de conocimiento siguen sin commit.
- `pyproject.toml` en **0.4.0**. **146 tests sin GPU** pasan (más 4 `gpu`); contratos sincronizados.

## Qué está corriendo en el equipo

| Pieza | Dónde | Notas |
|---|---|---|
| Overlay (bandeja, panel, editor, tareas) | `pythonw -m overlay`, arranca con Windows | relanzado con todo el código de v0.4.0 |
| voice-core | `127.0.0.1:8765`, oculto | lo lanza y lo cierra el overlay |
| tasks-service | `127.0.0.1:8766`, oculto | lo lanza el overlay; vigila la 5090 por UUID (`[tareas] gpu`) |
| LM Studio (servidor) | `127.0.0.1:1234` | lo arrancó tasks-service; sin modelos cargados al cerrar |

Modelo de tareas: `openai/gpt-oss-20b` con `reasoning_effort=high`. Se carga en la 5090 solo si está libre (≤ 1 GB y
≤ 10 % de cómputo) y se descarga al vaciarse la cola; el TTL de 120 s queda como red de seguridad. En la base hay
tareas reales del usuario (`datos/tareas.db`, fuera de git).

Configuración personal tocada esta sesión (fuera de git): `config/voice-core.toml` (`[tareas] gpu` = UUID de la 5090,
`ttl_s = 120`, `descargar_al_terminar`, umbrales, `[overlay] tareas` y `editor`) y `config/glosario.txt` (dos
términos más).

## Dónde nos quedamos exactamente

F4 cerrada y documentada. Lo único sin probar en vivo es lo último que se desplegó: que las **ayudas de los iconos del
panel** salgan al pasar el ratón y que **`Ctrl+Alt+E`** abra y oculte el editor. El usuario tiene los comandos de
commit y push.

## Cómo retomar sin gastar contexto

1. `git log -1 --oneline`: si F4 no está subido, recordárselo al usuario (comandos en la bitácora de la sesión 2).
2. Preguntar si las ayudas del panel y `Ctrl+Alt+E` funcionan; si algo falla, mirar `datos/overlay.log`.
3. Empezar F5 como siempre: preguntas al usuario → especificación `docs/F5_*.md` → código y tests → prueba en vivo.

## Prompt para abrir la siguiente sesión

```text
/transcriptor-start-session — Hoy: confirmo si las ayudas de los iconos del panel y Ctrl+Alt+E funcionan (y si subí F4). Seguimos con F5: pregúntame qué quiero primero (MCP, webhooks/carpeta .md, limpieza con LLM).
```

Al terminar la sesión: `/transcriptor-close-session`.
