# F1 · voice-core MVP

Estado: **implementada y verificada el 2026-10-21**, con audio grabado y en una prueba real con micrófono y hotkey
(ver «Prueba real»).

## Objetivo

Un servicio local que, al pulsar una hotkey, graba el micrófono, transcribe en vivo con el motor elegido en F0
(large-v3 + glosario en la 5080, o CPU de respaldo), guarda cada sesión y publica eventos que cualquier otro servicio
puede consumir por REST o WebSocket. El motor solo existe mientras se usa y se apaga solo.

## No objetivos

- Sin ventana flotante ni editor (F2 y F3). En F1 el feedback es un sonido al empezar y al terminar, y el CLI.
- Sin capturas de pantalla ni imágenes (F3).
- Sin organizador de tareas (F4): el modo `tarea` solo etiqueta la sesión.
- Sin edición del texto (`PATCH`), sin MCP ni webhooks (F3–F5).
- Sin autenticación: el servicio escucha solo en `127.0.0.1`.

## Arquitectura

```
hotkeys / API ──► GestorSesiones ──► FuenteMicrofono (16 kHz) ──► Motor ──(pipe)──► stt-worker (proceso hijo)
                       │                                            │                 VAD Silero + faster-whisper
                       ▼                                            ▼
                 Almacen (SQLite) ◄────────── Bus de eventos ◄── parcial / final
                                                   │
                                     REST /events · WebSocket /ws
```

- **Supervisor** (proceso principal): FastAPI, hotkeys, micrófono, SQLite y bus. Nunca importa CUDA.
- **stt-worker**: proceso hijo `spawn`. Fija `CUDA_VISIBLE_DEVICES` al UUID de la 5080 (o `-1` en CPU) antes de importar
  CTranslate2 y aborta si ve otra cantidad de GPUs que 1. Recibe audio, segmenta por pausas con VAD y devuelve
  texto parcial y final. Se cierra para liberar la VRAM.

## Máquina de estados del motor

| Estado | Significa | Pasa a |
|---|---|---|
| `apagado` | sin worker, 0 VRAM | `arrancando` al iniciar una sesión |
| `arrancando` | worker cargando; el audio se guarda en un búfer | `dictando` (o `en_espera` si la sesión ya terminó) al recibir `listo` |
| `dictando` | sesión activa | `en_espera` al terminar la sesión |
| `en_espera` | worker cargado, micrófono cerrado | `dictando` al iniciar sesión · `apagado` por inactividad, VRAM baja, `liberar` o cambio de modo |

Nunca se apaga en `dictando`. Si el worker muere, se publica `engine.error` y el estado vuelve a `apagado`.

## Modos

| Modo | Dispositivo al arrancar |
|---|---|
| `auto` (defecto) | 5080 si su VRAM libre ≥ `vram_minima_mib` (3072); si no, CPU |
| `gpu` | 5080; si no hay espacio, la sesión falla con 409 y `engine.error` |
| `cpu` | CPU, solo texto final |
| `pausado` | no se aceptan sesiones; libera el worker |

En `en_espera`, si la VRAM libre de la 5080 baja de `vram_ceder_mib` (1024), el motor cede la GPU y se apaga.

## Contratos (fuente de verdad: `src/voice_core/contratos.py`)

Exportados a `contracts/openapi.json` y `contracts/eventos.schema.json` con `scripts/exportar_contratos.py`;
un test falla si se desincronizan.

### REST (`http://127.0.0.1:8765`)

| Método y ruta | Cuerpo | Respuesta | Errores |
|---|---|---|---|
| `GET /health` | — | `HealthOut` | — |
| `GET /engine` | — | `EngineStatusOut` | — |
| `PUT /engine/mode` | `EngineModeIn` | `EngineStatusOut` | 422 |
| `POST /engine/release` | — | `EngineStatusOut` | 409 si está dictando |
| `POST /sessions` | `SessionStartIn` | `SessionOut` (201) | 409 si ya hay sesión, motor pausado o GPU sin espacio |
| `POST /sessions/toggle` | `SessionStartIn` | `SessionOut` | 409 como arriba |
| `POST /sessions/{id}/stop` | — | `SessionOut` (estado `procesando`) | 404, 409 si no es la activa |
| `GET /sessions` | `?limit=20&status=` | `list[SessionOut]` | 422 |
| `GET /sessions/{id}` | — | `SessionDetailOut` | 404 |
| `GET /sessions/{id}/export` | `?format=md\|txt` | texto | 404, 422 |
| `GET /events` | `?after=0&limit=200` | `list[Evento]` | 422 |
| `WS /ws` | `?after=N` | reenvía los eventos guardados con `seq > N` y luego los nuevos | — |

### Eventos

Todos llevan `type`, `ts` (UTC) y `seq`. Los persistentes tienen `seq` creciente y se pueden recuperar con
`/events?after=`; `segment.partial` es efímero (`seq = null`, solo por WebSocket).

| `type` | Datos |
|---|---|
| `session.started` | `session_id`, `mode` |
| `segment.partial` | `session_id`, `text` (frase en curso) |
| `segment.final` | `session_id`, `index`, `t0`, `t1`, `text` |
| `session.finalized` | `session_id`, `text`, `duration_s`, `segments` |
| `engine.state` | `state`, `mode`, `device`, `reason` |
| `engine.error` | `message`, `session_id?` |

### Base de datos

SQLite en `datos/voice-core.db` (WAL), migraciones SQL en `src/voice_core/migraciones/` aplicadas con
`PRAGMA user_version`. Tablas `sessions`, `segments` y `events`. El audio de cada sesión se guarda en
`datos/sesiones/<id>/audio.wav` (16 kHz, ≈ 1,9 MB/min; se desactiva con `guardar_audio = false`).

## Configuración

`config/voice-core.toml` (todas las claves tienen valor por defecto) y `config/glosario.txt` (frase natural en
español, con tildes; lo aprendido en F0).

## Criterios de aceptación

- [x] 1. `python -m voice_core` arranca en `127.0.0.1:8765` con el motor `apagado` y 0 VRAM en la 5080.
- [x] 2. `Ctrl+Alt+D` (o `POST /sessions`) abre el micrófono al instante; el motor pasa por `arrancando` a
   `dictando` y no se pierde el audio dicho durante el arranque.
- [x] 3. En la 5080 llegan `segment.partial` mientras se habla y un `segment.final` tras cada pausa (≥ 1 s; era
   0,7 s hasta la segunda prueba real).
- [x] 4. Al detener, llega `session.finalized` con el texto completo; la sesión queda en SQLite y
   `GET /sessions/{id}/export?format=md` devuelve Markdown.
- [x] 5. `GET /events?after=N` y `WS /ws?after=N` devuelven, en orden y sin huecos, los eventos persistentes posteriores a N.
- [x] 6. Tras `inactividad_min` sin uso, el worker se cierra y no queda ningún proceso nuestro en la 5080.
- [x] 7. `POST /engine/release` libera al momento fuera de un dictado y responde 409 durante un dictado.
- [x] 8. En `auto` con poca VRAM libre (o en `cpu`), el worker corre en CPU y solo emite texto final.
- [x] 9. El worker nunca ve la 5090: comprueba que hay exactamente 1 GPU visible y aborta si no.
- [x] 10. Los tests unitarios pasan sin GPU; el test de punta a punta con GPU transcribe un dictado grabado en F0.
- [x] 11. `contracts/` coincide con los modelos (test de deriva).

## Resultados (2026-10-21)

**Tests:** 48 unitarios sin GPU (segmentador, motor, API, almacén, contratos, hotkeys) y 3 de punta a punta con el
modelo real (`-m gpu`): 5080 con motor precalentado, arranque en frío y CPU. Todos pasan.

**Prueba de humo** con el servicio real (`--fuente-archivo dictado_10.wav`, inactividad de 30 s):

| t (s) | Evento |
|---|---|
| 0,65 | `engine.state arrancando` + `session.started` (el audio ya se guarda en el búfer) |
| 5,97 | `engine.state dictando` (modelo listo en 4,9 s) |
| 6,1 → 10,0 | 8 parciales, cada ≈ 0,5 s: «El fin…» → «…pero sigue habiendo overfitting.» |
| 11,47 | `segment.final`: «El fine-tuning con LoRA mejoró bastante del F1 en el conjunto de validación, pero sigue habiendo overfitting.» |
| 11,71 | `session.finalized` + `engine.state en_espera` |
| +37 | `engine.state apagado` por inactividad (30 s + revisión cada 5 s) |

- Motor cargado: +2212 MiB en la 5080; 5090 sin cambios (10 → 10 MiB); tras apagarse no queda ningún proceso
  `stt-worker`.
- Arranque en frío: lo dicho mientras carga el modelo (≈ 5 s) no se pierde: el texto final empieza en la primera palabra.
- En CPU: carga ≈ 10 s y el texto final llega ≈ 5 s después de cada pausa; sin parciales.

**Problemas encontrados y corregidos durante F1:**

1. *Alucinación en el primer parcial:* con 0,4 s de audio, Whisper escribió «Subtítulos en español por José Miguel
   Pinto…». Se corrigió de tres formas: el primer parcial espera 0,8 s de frase (`parcial_min_s`), se descartan los
   segmentos que Whisper marca como no-voz (`no_speech_prob > 0,6` y `avg_logprob < −1`) y el filtro de créditos
   reconoce esa variante sin bloquear la palabra «subtítulos» en un dictado normal.
2. *Hotkey ocupada:* `Ctrl+Alt+Espacio` la tiene registrada otra aplicación en este PC. El valor por defecto pasó a
   `Ctrl+Alt+D`; también están libres `ctrl+shift+space`, `ctrl+alt+enter`, `ctrl+alt+f9`, `ctrl+alt+r` y `pause`.

**Observaciones:**

- La 5080 es la que mueve el escritorio (Chrome, Firefox, Teams, Word, VS Code…), así que su VRAM «usada» cambia sola
  en ±100 MiB. Por eso el criterio 6 se comprueba por procesos y no por igualdad exacta de memoria.
- Los parciales son provisionales y pueden bailar («…del F1 en el F2», «…en Echo») hasta que llega el final; el overlay
  de F2 debe mostrarlos en gris y reemplazarlos.

## Prueba real con micrófono (2026-10-21, 16:38–16:42)

Tres sesiones con `Ctrl+Alt+D` y el ZOOM U-22: 23 frases, todas transcritas. Desde que terminas una frase hasta que
llega su texto final pasan **0,7–1,6 s** (mediana ≈ 1,0 s: los 0,7 s de pausa que la cierran más ≈ 0,3 s de
transcripción). El primer arranque tardó 5 s y no se perdió nada de lo dicho.

Lo que salió mal y cómo quedó:

| Qué pasó | Causa | Cambio |
|---|---|---|
| «No puedo pausar el servicio»: Ctrl+C en el visor no liberó la GPU | Ctrl+C cerró solo el visor; el servicio seguía en otra pestaña | Atajo **Ctrl+Alt+L** para liberar la GPU desde cualquier ventana (si estás dictando, primero cierra la sesión); el visor y el servicio lo avisan al arrancar |
| Las sesiones 2 y 3 «no funcionaban» | Sí se transcribieron, pero el visor ya estaba cerrado: no había dónde verlas | Mensaje al cerrar el visor: «el servicio sigue corriendo». La solución de fondo es el overlay (F2) |
| La 5080 seguía con 4,2 GB tras terminar | Por diseño, el modelo quedaba cargado 10 min en `en_espera` | Inactividad bajada a **2 min** (elección del usuario) |
| «Me gustaría ver un párrafo largo, no línea a línea» | El visor imprimía una línea por frase | El visor muestra un párrafo continuo con el texto provisional en tenue debajo (`--lineas` para el modo antiguo). Requisito también para F2 |

## Segunda prueba real (2026-10-21, 16:52–16:56)

Dos sesiones (29 frases, 189 s) con el visor en párrafo y `Ctrl+Alt+L` al final. Todo funcionó: el párrafo se ve
continuo, la latencia se mantuvo en **0,8–1,1 s** por frase y `Ctrl+Alt+L` apagó el motor 7 s después de terminar
(la 5080 volvió a ≈ 2,0–2,3 GB, que es solo el escritorio).

Problema visible: **frases partidas por pausas para pensar** («una pausa muy... Grande. Innecesaria.»). Con 0,7 s de
pausa la frase se cerraba y cada trozo se transcribía por separado, así que Whisper ponía «...» y mayúscula al
siguiente. Se reprocesó el audio real de la sesión (149 s) con `spikes/f1/contexto.py`:

| Variante | Frases | «...» | Mayúsculas a mitad de frase | Espera tras la pausa |
|---|---|---|---|---|
| A · 0,7 s (antes) | 24 | 3 | 4 | ≈ 0,9 s |
| B · 0,7 s + texto previo como contexto | 24 | 3 | 4 | ≈ 0,9 s |
| **C · 1,0 s (elegida)** | **15** | **1** | **2** | **≈ 1,2 s** |
| D · 1,0 s + contexto | 15 | 0 | 2 | ≈ 1,2 s |

- **C** une lo que se partía («una pausa muy grande, innecesaria, y corta las dos frases») y, al tener más audio por
  trozo, corrige palabras («cambia y pad» → «cambia y parece»).
- **El contexto (B, D) se descarta:** arregla algunas mayúsculas pero inventa o repite texto («y... desaparece»,
  «Esta primera prueba» en lugar de «Este es el primer error», o la misma frase dos veces). Es el riesgo conocido de
  realimentar a Whisper con su propia salida.
- Quedan cortes ocasionales con mayúscula («probar... Los diferentes shortcuts»); se dejan para una limpieza opcional
  del párrafo con LLM (F5).

También se corrigió: el visor ya no se cae si el servicio se reinicia (reconecta cada 2 s y recupera los eventos que
se perdió con `?after=`).

## Cómo usarlo

```powershell
# Arrancar el servicio (deja la consola abierta; Ctrl+C EN ESA consola lo detiene)
.venv\Scripts\python -m voice_core

# En otra consola: ver el texto en vivo (Ctrl+C aquí solo cierra el visor)
.venv\Scripts\voice-core-cli seguir
```

| Atajo | Hace |
|---|---|
| **Ctrl+Alt+D** | empieza o termina un dictado (suena un aviso en cada caso) |
| **Ctrl+Alt+T** | igual, pero la sesión queda etiquetada como `tarea` |
| **Ctrl+Alt+L** | libera la 5080 al instante; si estás dictando, primero cierra la sesión |

El modelo queda cargado 2 min tras cada dictado (volver a dictar en ese rato es instantáneo) y luego se libera solo.

| Para… | Comando |
|---|---|
| Ver el estado del motor | `.venv\Scripts\voice-core-cli estado` |
| Liberar la 5080 ya | `Ctrl+Alt+L` o `.venv\Scripts\voice-core-cli liberar` |
| Forzar CPU o pausar | `.venv\Scripts\voice-core-cli modo cpu` · `modo pausado` · `modo auto` |
| Listar sesiones | `.venv\Scripts\voice-core-cli sesiones` |
| Exportar una sesión | `.venv\Scripts\voice-core-cli exportar <id>` (Markdown) o `--txt` |
| Probar sin micrófono | `.venv\Scripts\python -m voice_core --fuente-archivo datos\f0\audios\dictado_02.wav` |
| Tests | `.venv\Scripts\python -m pytest` · con GPU: `-m gpu -s` |
| Regenerar contratos | `.venv\Scripts\python scripts\exportar_contratos.py` |

La documentación interactiva de la API queda en <http://127.0.0.1:8765/docs> mientras el servicio corre.

## Cambios posteriores (F2)

El contrato creció de forma compatible durante F2 (detalle en [F2_OVERLAY.md](F2_OVERLAY.md)):

- Estado `en_reposo` del motor (modelo en RAM tras `inactividad_min`, durante `reposo_min`) y campo `rest_in_s`.
- `POST /shutdown` para un cierre ordenado y `POST /engine/release?stop_session=true`.
- Los WebSocket abiertos ya no bloquean el apagado (cierre 1001).

## Pendiente

1. Probar la convivencia con otro proceso pesado en la misma GPU (p. ej. un TTS) (heredado de F0).
