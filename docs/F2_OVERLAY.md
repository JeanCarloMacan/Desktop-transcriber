# F2 · Ventana flotante (overlay), bandeja y arranque con Windows

Estado: **cerrada el 2026-10-21**. Probada por el usuario en tres rondas; arranca con Windows desde la carpeta de Inicio.
Quedan por confirmar en uso real el pegado con `Ctrl+Alt+V` y «Salir» desde la bandeja (criterios 5 y 7).

## Objetivo

Ver el dictado en vivo sobre la ventana que estás revisando, sin que te quite el foco, y tener el texto listo para
pegar al terminar. Todo arranca con Windows desde la bandeja del sistema, sin consolas.

## Decisiones del usuario

| Tema | Decisión |
|---|---|
| Visibilidad | Aparece al dictar; al terminar queda 15 s y se desvanece (no si tienes el ratón encima). `Ctrl+Alt+O` la muestra u oculta. En reposo solo hay un icono en la bandeja. |
| Al terminar | El texto se copia solo al portapapeles. `Ctrl+Alt+V` pega el último dictado donde está el cursor. |
| Arranque | Con Windows, en la bandeja, sin consolas. |
| Tamaño (2.ª prueba: «el recuadro es muy pequeño») | 680 px y 13 pt por defecto; ancho, alto (por bordes y esquinas) y letra (Ctrl+rueda) ajustables y recordados. |
| Alto (3.ª prueba: «solo puedo agrandarlo en horizontal; se pierde el principio») | Tamaño fijo como una nota adhesiva, redimensionable en vertical, con desplazamiento por rueda que no te arrastra si estás leyendo más arriba. |
| Arranque lento (2.ª prueba: 11,2 s) | **Reposo en RAM 30 min**: tras 2 min sin dictar el modelo pasa a RAM y despierta en < 1 s. |

## No objetivos

- Sin edición del texto ni capturas de pantalla (F3).
- Sin organizador de tareas (F4).
- El overlay no transcribe: es un cliente más de la API de voice-core (consumidor 1).

## Arquitectura

```
Inicio de Windows ──► pythonw -m overlay  (sin consola; instancia única por mutex)
                         ├─ si voice-core no responde, lo lanza oculto (python + CREATE_NO_WINDOW) y es su dueño
                         ├─ WebSocket /ws ──► EstadoPanel (lógica pura) ──► Panel (Qt) + icono de bandeja
                         ├─ REST: /engine, /engine/mode, /engine/release?stop_session=true, /sessions, /shutdown
                         └─ atajos propios: Ctrl+Alt+O (panel) y Ctrl+Alt+V (pegar)
voice-core (oculto) ──► Ctrl+Alt+D / T / L, el micrófono y el motor, como en F1
```

| Módulo (`src/overlay/`) | Qué hace |
|---|---|
| `estado.py` | Lógica pura (sin Qt): traduce eventos en lo que se muestra (título, párrafo, aviso, color) y en acciones (mostrar, copiar, programar el ocultado, consultar el motor). |
| `panel.py` | Ventana Qt sin marco, siempre encima, translúcida, con `WS_EX_NOACTIVATE` (los clics no le quitan el foco a tu aplicación). Tamaño de nota fija, bordes y esquinas para redimensionar, rueda para desplazar, Ctrl+rueda para la letra. |
| `bandeja.py` | Icono del color del estado y menú: mostrar, copiar, pegar, modo, liberar GPU, carpeta de datos, registro, salir. |
| `cliente.py` | WebSocket con reconexión y recuperación por `?after=`, y peticiones REST en hilos; resultados por señales de Qt. |
| `servicio.py` | Lanza voice-core oculto si no está y lo cierra ordenadamente (`POST /shutdown`) al salir. |
| `escritorio.py` | Win32: estilo sin activación, `Ctrl+V` en la ventana activa esperando a que sueltes Ctrl/Alt, mutex de instancia única. |
| `__main__.py` | Controlador: une todo, atajos propios, temporizadores (cuenta atrás y desvanecido). |

## Cambios en voice-core durante F2

| Cambio | Por qué |
|---|---|
| Estado nuevo `en_reposo` (modelo en RAM) y campo `rest_in_s` en `EngineStatusOut` | Arranque en < 1 s sin ocupar la 5080 (≈ 250–380 MB en lugar de 2,2 GB). |
| Mensajes `dormir`/`despertar` del worker (`unload_model(to_cpu=True)` / `load_model()` de CTranslate2) | Mover el modelo entre GPU y RAM sin cerrar el proceso. |
| `reposo_min` (30) en la configuración | Tras 2 min en la GPU, 30 min en RAM; luego apagado completo. 0 = sin reposo. |
| `POST /shutdown` (202) | Cierre ordenado desde el overlay: termina el dictado, cierra el worker y para el servidor. |
| `POST /engine/release?stop_session=true` | El botón ⏻ del panel se comporta como `Ctrl+Alt+L`: si estás dictando, primero cierra la sesión. |
| Registro solo a archivo si no hay consola (`pythonw`) | Con `pythonw` no existe `stderr`. |

### Ciclo de vida del motor (actualizado)

| Tiempo sin dictar | Estado | Modelo | 5080 | Siguiente dictado empieza en |
|---|---|---|---|---|
| 0–2 min | `en_espera` (punto azul) | GPU | ≈ 2,2 GB | al instante |
| 2–32 min | `en_reposo` (punto violeta) | RAM | ≈ 250–380 MB | **0,37 s** (medido con el modelo real) |
| > 32 min | `apagado` (punto gris) | — | 0 | 5–11 s (lo dicho no se pierde) |

`Ctrl+Alt+L`, el botón ⏻, la VRAM libre por debajo de 1 GB o pasar a modo CPU/pausa apagan del todo en cualquier
momento fuera de un dictado. En CPU no hay reposo: se apaga a los 2 min.

## Criterios de aceptación

- [x] 1. `pythonw -m overlay` arranca sin consola, lanza voice-core oculto si no estaba y muestra el icono en la
  bandeja. *(Probado con el acceso directo de Inicio: voice-core responde a los 2,7 s; ninguna ventana de consola,
  tampoco del worker.)*
- [x] 2. Al pulsar `Ctrl+Alt+D` el panel aparece sin quitar el foco a la ventana activa y muestra el estado del motor.
- [x] 3. El texto aparece como párrafo continuo: finales en blanco, provisional en gris, reemplazado en el sitio.
- [x] 4. Al terminar, el texto queda en el portapapeles y el panel se desvanece a los 15 s (no con el ratón encima).
- [~] 5. `Ctrl+Alt+V` pega el último dictado en la ventana activa aunque sigas sujetando Ctrl y Alt. *(Implementado
  y registrado; falta confirmación explícita en una prueba real.)*
- [x] 6. `Ctrl+Alt+O` muestra u oculta el panel con el último texto.
- [~] 7. Desde la bandeja se cambia el modo, se libera la GPU y se sale; al salir no queda ningún proceso. *(El cierre
  ordenado está probado por la API —sin procesos residuales—; falta confirmar el clic en «Salir» del menú.)*
- [x] 8. El panel recuerda posición, tamaño y letra, y vuelve a una posición visible si la guardada quedó fuera.
- [x] 9. Un acceso directo en la carpeta de Inicio de Windows lo arranca al iniciar sesión.
- [x] 10. La lógica del panel tiene tests sin Qt; el panel se renderiza en tests sin mostrarse en pantalla.

## Pruebas

- **80 tests sin GPU** (`.venv\Scripts\python -m pytest`): estado del panel, panel Qt en plataforma *offscreen*
  (alto fijo, redimensión vertical y por esquinas, límite de pantalla, rueda que no arrastra, letra, persistencia),
  motor con reposo (dormir, despertar, apagado a los 32 min, cesión de VRAM, CPU sin reposo), API (`/shutdown`,
  `stop_session`, WebSocket que no bloquea el cierre) y los de F1.
- **4 tests con el modelo real** (`-m gpu -s`): 5080, arranque en frío, CPU y el ciclo de reposo
  (cargado +2329 MiB → reposo +377 MiB → despierta en 0,37 s y transcribe bien).
- **Revisión visual:** el panel se renderiza con las fuentes reales de Windows sin mostrarse
  (`QWidget.grab()`), en los estados dictando, terminado, en reposo y cargando.

## Problemas encontrados y corregidos

| Problema | Causa | Corrección |
|---|---|---|
| Arranque de 11,2 s en la 2.ª prueba | Con apagado a los 2 min, casi cada dictado cargaba el modelo desde el disco | Reposo en RAM (despierta en 0,37 s) |
| Recuadro pequeño | 480 px y 11 pt | 680 px y 13 pt, y todo ajustable |
| Se perdía el principio del texto | Solo se veía la cola, sin poder desplazar | Alto fijo redimensionable + rueda que no te arrastra si lees más arriba |
| Asomaba una franja de la línea anterior en la vista compacta | El alto no caía en una línea entera (métrica de fuente y margen superior) | Alto calculado con la línea maquetada real y solo el margen inferior |
| 20 px vacíos bajo el texto | El pie vacío contaba al medir y se ocultaba después | Primero se fija el pie y luego se mide |
| `/shutdown` se quedaba colgado con un cliente conectado | uvicorn esperaba sin límite a que cerrara el WebSocket | El bus suelta a los suscriptores (cierre 1001) y `timeout_graceful_shutdown=3`; ahora cierra en 0,4 s |
| Aviso de Qt «Point size <= 0» | Fuente de iconos en píxeles | Fuente en puntos |

## Cómo usarlo

Arranca solo al iniciar sesión. Para arrancarlo a mano: doble clic en
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Transcriptor RT.lnk`, o `.venv\Scripts\pythonw -m overlay`.

| Atajo o gesto | Hace |
|---|---|
| `Ctrl+Alt+D` | empieza o termina un dictado |
| `Ctrl+Alt+T` | igual, etiquetado como `tarea` |
| `Ctrl+Alt+V` | pega el último dictado donde está el cursor |
| `Ctrl+Alt+O` | muestra u oculta el panel |
| `Ctrl+Alt+L` | libera la GPU del todo |
| Arrastrar el panel | lo mueve |
| Arrastrar bordes o esquinas | cambia ancho y alto (el alto, en líneas enteras) |
| Rueda | desplaza el texto |
| Ctrl + rueda | cambia el tamaño de la letra |
| Clic en el icono de la bandeja | muestra u oculta el panel; clic derecho, menú completo |

| Para… | Comando |
|---|---|
| Quitar el arranque con Windows | `powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1 -Quitar` |
| Volver a instalarlo | `powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1` |
| Ver los registros | `datos\overlay.log`, `datos\voice-core.log`, `datos\voice-core.salida.log` |
| Restablecer posición y tamaño del panel | borrar `datos\overlay.json` |

Configuración en `config/voice-core.toml` (secciones `[motor]` y `[overlay]`).
