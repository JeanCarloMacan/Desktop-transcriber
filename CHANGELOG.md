# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones con [SemVer](https://semver.org/lang/es/).

## [0.3.0] · 2026-10-21 · F3

### Añadido
- `Ctrl+Alt+S` abre el recorte de Windows y adjunta la imagen al dictado en curso, en el instante en que pulsaste (sin
  dictado activo, al último). El panel marca `[📷 N]` en el texto en vivo.
- Documento de cada sesión en Markdown con las capturas tras la frase que se estaba diciendo; se escribe en
  `datos/sesiones/<id>/documento.md` junto a `capturas/NNN.png`.
- Editor de dictados (botón ✎ del panel o menú de la bandeja): lista de sesiones, corrección con las imágenes en su
  sitio, guardado automático, «Volver al original» e «Insertar captura» en el cursor.
- «Copiar para chat» (texto con `[Imagen N]` + arrastrar todas las imágenes a la vez) y «Copiar para agente» (Markdown
  con rutas absolutas). Al terminar un dictado con capturas se copia la versión para chat; `Ctrl+Alt+V` pega la
  corregida si la hay.
- API: `POST/GET /sessions/{id}/attachments`, `GET/PUT/DELETE /sessions/{id}/document`, `export?format=chat`; campos
  `attachments` y `edited_at` en `SessionOut` y `attachment_items` en el detalle; eventos `attachment.added` y
  `session.edited`.
- Migración `0002_adjuntos_documento.sql` (tabla `attachments`, columnas `document` y `edited_at`), aplicada sola sobre
  las bases anteriores.

### Corregido
- Una captura insertada con el cursor en el título heredaba el formato de encabezado.
- Los párrafos guardados desde el editor llegaban partidos en líneas de 80 caracteres.

## [0.2.0] · 2026-10-21 · F2

### Añadido
- Panel flotante (PySide6): siempre encima, translúcido, sin robar el foco; texto en párrafo continuo con el
  provisional en gris; tamaño de nota redimensionable por bordes y esquinas; rueda para desplazar y Ctrl+rueda para la
  letra; recuerda posición y tamaño.
- Icono en la bandeja con el color del estado y menú (modo, liberar GPU, carpeta de datos, registro, salir).
- `Ctrl+Alt+V` pega el último dictado en la ventana activa; `Ctrl+Alt+O` muestra u oculta el panel; copia automática
  al portapapeles al terminar.
- Estado `en_reposo`: tras 2 min el modelo pasa a RAM y vuelve a la GPU en ≈ 0,4 s; apagado completo a los 30 min.
- `POST /shutdown` y `POST /engine/release?stop_session=true`; campo `rest_in_s` en `GET /engine`.
- Arranque con Windows sin consolas (`scripts/instalar_inicio.ps1`).
- GPU elegida por nombre (`motor.gpu = "5080"`) o UUID; si es ambiguo, CPU.
- `voice-core --descargar-modelo`; plantillas `config/*.example.*`.
- Documentación: arquitectura, API, stack, desarrollo y diagramas.

### Corregido
- `/shutdown` se quedaba esperando si había un WebSocket abierto.
- Alturas del panel: franja de la línea anterior visible y hueco por el pie vacío.

## [0.1.0] · 2026-10-21 · F0 + F1

### Añadido
- Banco de pruebas de motores (F0): large-v3 + glosario elegido con dictados reales (3,2 % WER).
- Servicio `voice-core`: atajos globales, micrófono, VAD Silero, transcripción en vivo (parcial + final), sesiones en
  SQLite con su audio, bus de eventos con `seq`, API REST + WebSocket con recuperación por `?after=`, contratos
  OpenAPI y JSON Schema, motor en proceso aparte que se apaga solo y nunca ve más que la GPU permitida.
- `voice-core-cli` con visor de texto en vivo.

### Cambiado
- Pausa que cierra una frase de 700 a 1000 ms tras las pruebas reales (menos frases partidas).
