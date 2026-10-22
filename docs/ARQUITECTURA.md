# Arquitectura

Transcriptor RT son **dos programas** que se hablan por HTTP/WebSocket en `127.0.0.1:8765`:

- **voice-core** (`src/voice_core/`): el servicio. Graba, transcribe, guarda y publica eventos. No tiene interfaz.
- **overlay** (`src/overlay/`): el panel flotante y el icono de la bandeja. Es solo un cliente más de voice-core.

Esa separación es deliberada: cualquier otro programa (un organizador de tareas, un servidor MCP, un script) puede
consumir los dictados exactamente igual que el panel.

## Componentes

```mermaid
flowchart TB
    subgraph win["Windows"]
        mic(["Micrófono<br/>WASAPI"])
        teclado(["Atajos globales<br/>RegisterHotKey"])
        activa(["Ventana activa"])
        recorte(["Recorte de Windows<br/>ms-screenclip:"])
    end

    subgraph ov["overlay · pythonw (sin consola)"]
        ctl["Controlador"]
        est["EstadoPanel<br/>(lógica pura)"]
        pan["Panel Qt<br/>WS_EX_NOACTIVATE"]
        tray["Bandeja"]
        edi["Editor de dictados"]
        cap["Capturador<br/>portapapeles"]
        cli["Cliente<br/>WebSocket + REST"]
        ctl --> est --> pan
        ctl --> tray
        ctl --> edi
        ctl --> cap
        cli --> ctl
    end

    subgraph vc["voice-core · proceso oculto"]
        api["API FastAPI<br/>REST + /ws"]
        ses["GestorSesiones"]
        doc["Documento<br/>(lógica pura)"]
        mot["Motor<br/>máquina de estados"]
        bus["Bus de eventos<br/>seq creciente"]
        db[("SQLite<br/>sessions · segments<br/>attachments · events")]
        disco[("datos/sesiones/id/<br/>audio · capturas · documento.md")]
        fuente["FuenteMicrofono<br/>16 kHz mono"]
        ses --> fuente
        ses --> mot
        ses --> bus
        ses --> doc
        ses --> disco
        mot --> bus
        bus --> db
        api --> ses
        api --> mot
        api --> bus
    end

    subgraph wk["stt-worker · proceso hijo (solo mientras se usa)"]
        seg["Segmentador<br/>VAD Silero"]
        fw["faster-whisper large-v3<br/>CTranslate2 en la GPU permitida o CPU"]
        seg --> fw
    end

    mic --> fuente
    teclado --> ses
    mot <-- "pipes: audio · reiniciar · vaciar · dormir/despertar<br/>parcial · final · listo · dormido" --> seg
    api <-- "WebSocket /ws · REST" --> cli
    ctl -- "Ctrl+Alt+V: portapapeles + Ctrl+V" --> activa
    cap -- "Ctrl+Alt+S" --> recorte
    recorte -- "imagen en el portapapeles" --> cap
```

| Módulo | Responsabilidad |
|---|---|
| `voice_core/contratos.py` | Modelos Pydantic de la API y de los eventos: **fuente de verdad** del contrato. |
| `voice_core/api.py` | Rutas REST y WebSocket; ciclo de vida del servidor. |
| `voice_core/sesiones.py` | Une micrófono, motor, almacén y bus. Una sesión activa a la vez. Capturas y documento de cada sesión. |
| `voice_core/documento.py` | Lógica pura del documento: párrafos e imágenes por tiempo, re-unir párrafos, exportar para chat, agente o texto. |
| `voice_core/motor.py` | Elige dispositivo, lanza y cierra el worker, estados `apagado → arrancando → dictando ⇄ en_espera → en_reposo`. |
| `voice_core/worker.py` | El proceso hijo (`spawn`) con el modelo y su manejador en el supervisor. |
| `voice_core/segmentador.py` | Lógica pura: ventanas de 32 ms, VAD, frases, parciales y finales, filtro de alucinaciones. |
| `voice_core/almacen.py` + `migraciones/` | SQLite con migraciones SQL versionadas (`PRAGMA user_version`). |
| `voice_core/bus.py` | Persiste los eventos no efímeros y los reparte a los suscriptores. |
| `voice_core/gpu.py` | NVML (VRAM sin contexto CUDA), elección de la GPU por nombre o UUID, DLLs de CUDA. |
| `voice_core/hotkeys.py` | Atajos globales con `RegisterHotKey` en un hilo con bucle de mensajes. |
| `voice_core/audio.py` | Micrófono (PortAudio) y fuente de archivo para pruebas. |
| `overlay/estado.py` | Lógica pura del panel: eventos → qué se muestra y qué acciones tomar. |
| `overlay/panel.py`, `bandeja.py` | Interfaz Qt. |
| `overlay/editor.py` | Editor de dictados: lista de sesiones, documento con las imágenes, copiar para chat o agente, arrastrar las imágenes. |
| `overlay/capturas.py` | Lanza el recorte de Windows y espera la imagen en el portapapeles (o se rinde a los 60 s). |
| `overlay/cliente.py`, `servicio.py`, `escritorio.py` | Conexión con voice-core, su arranque/cierre y ayudas de Win32. |

## Un dictado de principio a fin

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuario
    participant H as Atajos (Win32)
    participant S as GestorSesiones
    participant M as Motor
    participant W as stt-worker
    participant B as Bus + SQLite
    participant O as Overlay

    U->>H: Ctrl+Alt+D
    H->>S: alternar("dictado")
    S->>M: encender()
    Note over M,W: si estaba apagado, lanza el worker (≈ 5 s);<br/>si estaba en reposo, lo despierta (≈ 0,4 s)
    M-->>B: engine.state · arrancando
    S->>B: session.started
    B-->>O: el panel aparece (sin quitar el foco)
    loop cada 50 ms
        S->>M: audio 16 kHz
        M->>W: audio (o al búfer mientras carga: no se pierde nada)
    end
    W-->>M: listo
    M-->>B: engine.state · dictando
    loop mientras hablas
        W-->>S: parcial (cada 0,4 s de audio)
        S-->>B: segment.partial (efímero, sin seq)
        B-->>O: texto provisional en gris
        W-->>S: final (tras 1 s de pausa)
        S->>B: segment.final (persistido con seq)
        B-->>O: texto definitivo
    end
    U->>H: Ctrl+Alt+D
    H->>S: detener()
    S->>M: vaciar
    W-->>S: último final + vaciado
    S->>B: session.finalized (texto completo, audio.wav)
    B-->>O: copia al portapapeles · se desvanece a los 15 s
    M-->>B: engine.state · en_espera
```

## Capturas y documento

Una captura se ata a **un instante** de la sesión (`t`, en segundos desde el inicio). El documento se arma ordenando
frases e imágenes por tiempo: cada imagen va tras la última frase que empezó antes de ella, que es la que estabas
diciendo al pulsar.

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuario
    participant O as Overlay
    participant W as Recorte de Windows
    participant S as GestorSesiones
    participant B as Bus + SQLite

    U->>O: Ctrl+Alt+S (dictando, a los 13,4 s)
    O->>O: recuerda sesión y t · oculta panel y editor
    O->>W: ms-screenclip:
    Note over S: el dictado sigue grabando mientras recortas
    U->>W: recorta un rectángulo
    W-->>O: imagen en el portapapeles
    O->>S: POST /sessions/{id}/attachments?t=13.4 (PNG)
    S->>S: valida la firma PNG · capturas/001.png
    S->>B: attachment.added
    B-->>O: «[📷 1]» en el texto en vivo
    U->>O: Ctrl+Alt+D (fin)
    S->>S: documento.md = frases e imágenes por tiempo
    S->>B: session.finalized
    B-->>O: copia la versión para chat ([Imagen 1])
```

```mermaid
flowchart LR
    gen["Documento generado<br/>segmentos + capturas por tiempo"] --> doc
    edit["Corrección en el editor<br/>PUT /document"] --> doc
    doc{{"Documento de la sesión<br/>Markdown, rutas relativas"}}
    doc --> md["documento.md<br/>en la carpeta de la sesión"]
    doc -- "export?format=md" --> agente["Agente<br/>rutas absolutas a las PNG"]
    doc -- "export?format=chat" --> chat["Chat<br/>texto con [Imagen N]<br/>+ arrastrar las PNG"]
    doc -- "export?format=txt" --> txt["Solo texto"]
```

- Mientras no se edita, el documento se **genera** cada vez; al guardarlo, se conserva el Markdown editado
  (`sessions.document`) y `DELETE /document` vuelve al generado. Una captura que llega después de editar se añade al
  final.
- El editor muestra el Markdown con `QTextEdit`, que corta las líneas a 80 columnas al exportarlo; el servicio re-une
  los párrafos al guardar y al exportar.
- Las imágenes nunca se borran: quitar una imagen del documento la saca de las exportaciones, pero el archivo sigue
  en `capturas/`.

## Ciclo de vida del motor

```mermaid
stateDiagram-v2
    [*] --> apagado
    apagado --> arrancando: dictar · carga ≈ 5 s (el audio se guarda en un búfer)
    arrancando --> dictando: modelo listo
    dictando --> en_espera: fin del dictado
    en_espera --> dictando: dictar · al instante
    en_espera --> en_reposo: 2 min sin uso · el modelo pasa a RAM
    en_reposo --> arrancando: dictar · vuelve a la GPU en ≈ 0,4 s
    en_reposo --> apagado: 30 min sin uso
    en_espera --> apagado: Ctrl+Alt+L · VRAM libre < 1 GB · modo CPU o pausa
    en_reposo --> apagado: Ctrl+Alt+L · VRAM libre < 1 GB
```

| Estado | Modelo | GPU ocupada | Siguiente dictado empieza en |
|---|---|---|---|
| `apagado` | — | 0 | 5–11 s |
| `en_espera` | GPU | ≈ 2,2 GB | al instante |
| `en_reposo` | RAM (`unload_model(to_cpu=True)`) | ≈ 250–380 MB (contexto CUDA) | ≈ 0,4 s |

Reglas: nunca se apaga a mitad de un dictado; en CPU no hay reposo (se apaga a los 2 min); si el worker muere al cargar
en GPU en modo `auto`, se reintenta en CPU conservando el audio encolado.

## Política de GPU

- **Una sola GPU permitida**, configurada en `motor.gpu` por nombre («5080») o UUID. Si el nombre no coincide con
  ninguna o coincide con varias, no se usa ninguna (CPU): es preferible a cargar el modelo donde no toca.
- El worker arranca con `CUDA_VISIBLE_DEVICES=<UUID>` **antes** de importar CTranslate2 y comprueba que ve exactamente
  1 GPU; si no, aborta. El supervisor nunca importa CUDA.
- La VRAM se vigila con **NVML**, que no crea contexto CUDA. Modo `auto`: GPU si quedan ≥ 3 GB libres; si no, CPU.
- **Ceder la GPU**: en espera o reposo, si la VRAM libre baja de 1 GB (otro programa la necesita), el motor se apaga.
- Liberar del todo exige **cerrar el proceso**: borrar el modelo deja ≈ 240 MB de contexto CUDA.

## Segmentación y transcripción

| Parámetro | Valor | Por qué |
|---|---|---|
| Ventana de VAD | 512 muestras (32 ms) | Silero VAD en streaming, con estado entre ventanas |
| Pausa que cierra una frase | 1000 ms | con 700 ms las pausas para pensar partían frases ([F1](F1_VOICE_CORE.md)) |
| Frase máxima | 28 s | Whisper trabaja en ventanas de 30 s |
| Primer parcial | tras 0,8 s de frase | con menos audio Whisper inventaba créditos de subtítulos |
| Parcial | cada 0,4 s, beam 1 | ≈ 0,16 s por refresco en la GPU |
| Final | beam 5 + glosario en `initial_prompt` | 3,2 % WER con jerga técnica ([F0](F0_SPIKE.md)) |
| Filtro | `no_speech_prob > 0,6` y `avg_logprob < −1`, más créditos conocidos («Subtítulos por…») | alucinaciones sobre silencio |

## Datos

```mermaid
erDiagram
    sessions ||--o{ segments : contiene
    sessions ||--o{ attachments : adjunta
    sessions ||--o{ events : genera
    sessions {
        TEXT id PK
        TEXT mode "dictado o tarea"
        TEXT status "grabando, procesando o final"
        TEXT started_at "ISO-8601 UTC"
        TEXT ended_at
        TEXT text "transcripción tal cual"
        TEXT device "cuda o cpu"
        TEXT model
        TEXT audio_path "datos/sesiones/id/audio.wav"
        TEXT document "Markdown editado, NULL = generado"
        TEXT edited_at
    }
    segments {
        TEXT session_id FK
        INTEGER idx
        REAL t0 "segundos desde el inicio"
        REAL t1
        TEXT text
    }
    attachments {
        INTEGER id PK
        TEXT session_id FK "ON DELETE CASCADE"
        TEXT kind "imagen"
        REAL t "segundos desde el inicio"
        TEXT file "capturas/001.png"
        INTEGER width
        INTEGER height
        TEXT created_at
    }
    events {
        INTEGER seq PK "orden global"
        TEXT ts
        TEXT type
        TEXT session_id FK
        TEXT payload "JSON del evento"
    }
```

- `datos/voice-core.db` (SQLite en modo WAL), `datos/overlay.json` (posición y tamaño del panel) y los registros
  `datos/*.log`. Nada de `datos/` va a git.
- Cada sesión tiene su carpeta `datos/sesiones/<id>/`: `audio.wav` (16 kHz, ≈ 1,9 MB/min, desactivable),
  `capturas/NNN.png` y `documento.md` (rutas relativas, para entregar la carpeta entera a un agente).
- Los eventos `segment.partial` son **efímeros**: solo viajan por WebSocket. El resto se persisten con `seq` y se
  pueden recuperar con `GET /events?after=N` o `WS /ws?after=N` (sin huecos aunque el cliente se reconecte).
- Tras un cierre brusco, las sesiones que quedaron abiertas pasan a `final` con lo que tengan al arrancar.

## Procesos e hilos

| Proceso | Hilos | Notas |
|---|---|---|
| **overlay** (`pythonw -m overlay`) | Qt (principal) · WebSocket · REST (uno por petición) · atajos · pegado | Mutex de instancia única; si voice-core no responde, lo lanza oculto (`python` + `CREATE_NO_WINDOW`) y lo cierra con `POST /shutdown` al salir. |
| **voice-core** (`python -m voice_core`) | bucle asyncio (FastAPI, bus, sesiones, motor) · audio (callback de PortAudio) · atajos · emisor y lector del pipe | Todo lo que llega de otros hilos entra al bucle con `call_soon_threadsafe`. |
| **stt-worker** (`multiprocessing`, `spawn`) | principal (VAD + transcripción) · recepción del pipe | Junta el audio atrasado en un lote: después de cargar procesa el búfer de una vez. Si el supervisor muere, el pipe da EOF y el worker sale. |

## Contratos

`src/voice_core/contratos.py` es la fuente de verdad. `scripts/exportar_contratos.py` genera
`contracts/openapi.json` y `contracts/eventos.schema.json`, y un test falla si se desincronizan. Ver [API.md](API.md).

## Configuración

Orden de búsqueda: `TRT_CONFIG` → `config/voice-core.toml` (personal, fuera de git) →
`config/voice-core.example.toml` (plantilla). Las rutas relativas parten de la raíz del proyecto. Todas las claves
tienen valor por defecto en `voice_core/ajustes.py`; las secciones son `servidor`, `datos`, `motor`, `segmentacion`,
`audio`, `hotkeys` y `overlay`.

## Decisiones principales

| Decisión | Alternativas descartadas | Dónde se justifica |
|---|---|---|
| faster-whisper `large-v3` `int8_float16` + glosario | turbo (3× más rápido pero 9,5 % WER con jerga), Parakeet, nube | [F0](F0_SPIKE.md) |
| Entorno propio sin PyTorch; cuBLAS del CUDA Toolkit | reutilizar otro venv (5 GB, acoplado) | [F0](F0_SPIKE.md) |
| Supervisor sin CUDA + worker en proceso hijo | todo en un proceso (no se puede liberar la VRAM del todo) | [F1](F1_VOICE_CORE.md) |
| Pausa de 1000 ms, sin contexto entre frases | 700 ms (frases partidas); texto previo como prompt (inventa y repite) | [F1](F1_VOICE_CORE.md) |
| Reposo del modelo en RAM 30 min | apagar a los 2 min (arranques de 5–11 s) | [F2](F2_OVERLAY.md) |
| Panel PySide6 como cliente de la API | Tauri, Electron, tkinter | [Plan](planes/plan-transcriptor-rt.svg), [F2](F2_OVERLAY.md) |
| Eventos con `seq` + recuperación por `?after=` | broker (NATS/Redis), webhooks | [Plan](planes/plan-transcriptor-rt.svg) |
| Capturas con el recorte de Windows (`ms-screenclip:`) | herramienta de recorte propia (más código que mantener; la de Windows ya deja anotar antes de copiar) | [F3](F3_EDITOR.md) |
| Documento en Markdown con las imágenes ordenadas por tiempo | HTML o un formato propio (Markdown lo leen los agentes y se pega legible en un chat); imágenes todas al final | [F3](F3_EDITOR.md) |
| Dos salidas para la IA: chat (`[Imagen N]` + arrastrar) y agente (rutas absolutas) | solo una de las dos | [F3](F3_EDITOR.md) |

Diagramas complementarios: [plan general](planes/plan-transcriptor-rt.svg) y
[recursos, modos y apagado](planes/recursos-y-modos.svg).
