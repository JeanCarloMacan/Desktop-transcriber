# F0 · Prueba de motores de voz (spike)

Estado: **cerrada el 2026-10-21.** Decisión: `large-v3` en `int8_float16` con glosario (ver «Decisión» al final).

## Objetivo

Elegir, con datos medidos en este PC, el modelo y la configuración del motor de voz para `voice-core`, cumpliendo las
restricciones de recursos: nunca la RTX 5090, la RTX 5080 solo si hay espacio, respaldo en CPU y poco disco extra.

## No objetivos

- No se construye la API, el overlay ni el supervisor (eso es F1 y F2).
- No se prueban motores fuera de faster-whisper (Parakeet o la nube quedan para después, si F0 no convence).
- No se modifica nada de otro proyecto local que ya usaba faster-whisper (pipeline de TTS) ni de su entorno.

## Restricciones

- Entorno propio (`.venv` con uv) y sin PyTorch.
- Modelos compartidos en una carpeta `<carpeta_modelos>/<org>--<repo>`, en carpetas normales sin symlinks (la caché de Hugging Face
  falla en Windows con WinError 1314). Es la misma convención que ya usaba ese otro proyecto, así que el modelo se comparte.
- DLLs de CUDA: las del CUDA Toolkit ya instalado (`%CUDA_PATH%\bin`), sin copiar ni descargar otras.
- GPU fijada por UUID con `CUDA_VISIBLE_DEVICES` antes de cargar CTranslate2.

## Configuraciones a comparar

| Id | Modelo | Dispositivo | Tipo de cómputo |
|---|---|---|---|
| v3-gpu-fp16 | large-v3 | 5080 | float16 |
| v3-gpu-int8 | large-v3 | 5080 | int8_float16 |
| turbo-gpu-fp16 | large-v3-turbo | 5080 | float16 |
| turbo-gpu-int8 | large-v3-turbo | 5080 | int8_float16 |
| turbo-cpu-8 | large-v3-turbo | CPU, 8 hilos | int8 |
| turbo-cpu-16 | large-v3-turbo | CPU, 16 hilos | int8 |
| v3-cpu-8 | large-v3 | CPU, 8 hilos | int8 |

## Qué se mide en cada configuración

Cada configuración corre en un proceso nuevo, de una en una:

- **Carga:** segundos y VRAM de la 5080 (por NVML).
- **Primera llamada en frío:** el calentamiento que pagaría el estado «Arrancando».
- **Pasada final** (beam 5) por clip: tiempo, factor de tiempo real, WER y CER contra el texto de referencia.
- **Pasada parcial** (beam 1, ventanas de 2 s y 4 s): tiempo medio, para saber si da para texto en vivo.
- **VRAM:** con el modelo borrado y el proceso vivo, y otra vez tras cerrar el proceso.
- **Control:** la VRAM de la 5090 antes y después de cada corrida tiene que ser idéntica.

## Audios

1. **Ya disponibles:** clips de referencia en español del otro proyecto (voz leída del autor, `.wav` con su `.txt`). Es voz
   leída, no dictado.
2. **Grabados por el usuario:** frases tipo dictado con jerga técnica (`spikes/f0/frases_f0.txt`), con `grabar.py`.

## Cómo correrlo

Todo desde la raíz del proyecto, con el entorno propio (`uv sync` lo crea en `.venv`, 244 MB):

| Paso | Comando |
|---|---|
| Comprobar CUDA en la GPU permitida | `.venv\Scripts\python spikes\f0\sonda_cuda.py` (GPU por `TRT_GPU`, parte del nombre, o `TRT_GPU_UUID`) |
| Ver micrófonos | `.venv\Scripts\python spikes\f0\grabar.py --listar` |
| Grabar tus audios | `.venv\Scripts\python spikes\f0\grabar.py` |
| Banco con clips de referencia | `$env:TRT_F0_REFERENCIAS = "<carpeta con .wav + .txt>"; .venv\Scripts\python spikes\f0\bench.py` |
| Banco con tus dictados | `.venv\Scripts\python spikes\f0\bench.py --set dictado` |

`bench.py` no carga CUDA: lanza cada configuración en un proceso nuevo con la GPU ya fijada por UUID (o `-1` en CPU)
y escribe en `datos\f0\resultados\<fecha>-<conjunto>\` un JSON por configuración y un `resumen.md`.

Archivos del spike en `spikes\f0\`:

- `entorno.py`: UUID de la 5080, DLLs de CUDA, VRAM por NVML y carpeta de modelos.
- `comun.py`: audio a 16 kHz, normalización y WER/CER.
- `bench_motor.py`: mide una configuración.
- `bench.py`: orquesta las configuraciones.
- `grabar.py`: graba tus audios.
- `frases_f0.txt`, `glosario.txt`: frases de prueba y glosario.

## Criterios de aceptación

- [x] El entorno propio carga CTranslate2 en la 5080 sin PyTorch y sin DLLs de otro proyecto.
- [x] En ninguna corrida cambia la VRAM de la 5090.
- [x] Al cerrar el proceso, la VRAM de la 5080 vuelve a su valor inicial (±50 MiB).
- [x] Hay una tabla con carga, frío, VRAM, factor de tiempo real, WER y CER de cada configuración.
- [x] Hay una recomendación de configuración para GPU y para CPU, con su justificación.
- [x] Se mide el efecto de un glosario (`initial_prompt`) sobre la jerga técnica.

## Resultados

### Corrida 1 · clips de referencia (2026-10-21)

Seis clips de tu voz leída en español (9 a 29 s; 82 s en total). Resultados completos en
`datos\f0\resultados\` (carpeta local, fuera de git).

| Config | Carga | 1.ª llamada | VRAM pico | Tras salir | Δ 5090 | WER | Frase de 12 s | Parcial (ventana 2 s) |
|---|---|---|---|---|---|---|---|---|
| **turbo-gpu-int8** | 2,2 s | 0,45 s | **1338 MiB** | −2 | 0 | 5,3 % | **0,23 s** | **0,09 s** |
| turbo-gpu-fp16 | 1,4 s | 0,44 s | 2395 MiB | 3 | 0 | 5,3 % | 0,23 s | 0,09 s |
| turbo-gpu-int8 + glosario | 2,2 s | 0,44 s | 1327 MiB | 13 | 0 | 5,3 % | 0,24 s | 0,08 s |
| v3-gpu-int8 | 4,0 s | 0,51 s | 2190 MiB | −1 | 0 | 5,3 % | 0,73 s | 0,18 s |
| v3-gpu-fp16 | 2,4 s | 0,50 s | 4111 MiB | 0 | 0 | 5,3 % | 0,64 s | 0,17 s |
| turbo-cpu-8 | 2,1 s | 3,70 s | 0 | — | 0 | 5,3 % | 3,9 s | 3,3 s |
| turbo-cpu-16 | 2,3 s | 3,65 s | 0 | — | 0 | 5,3 % | 4,1 s | 3,6 s |
| v3-cpu-8 | 3,7 s | 3,54 s | 0 | — | 0 | 5,3 % | 5,7 s | 3,5 s |

Lectura:

1. **Precisión idéntica en las ocho configuraciones** (WER 5,3 %, CER 2,5 %). Casi todos los «errores» son la
   referencia: en esas tomas improvisaste algo («qué es lo que pasa», «sin trucos y sin magia») y hay un «aun/aún». Estos
   clips no sirven para separar modelos por precisión; para eso hacen falta tus dictados con jerga.
2. **turbo es 3 veces más rápido que large-v3 en la 5080** (0,23 s frente a 0,73 s por frase de 12 s) y en int8_float16
   ocupa **1,3 GB** (la mitad que en float16, sin perder velocidad).
3. **El texto parcial en vivo es viable en la 5080:** releer los últimos 2–4 s cuesta ≈ 0,09–0,10 s con turbo, así que
   se puede refrescar cada ~300 ms con un único modelo (sin un segundo modelo pequeño).
4. **En CPU, cualquier llamada cuesta ≈ 3,3–3,7 s**, dure lo que dure el audio: Whisper rellena siempre a ventanas de
   30 s y turbo solo aligera el decodificador, no el codificador. Con 16 hilos no mejora (los núcleos E no ayudan).
   Conclusión: en CPU hay texto final (≈ 4 s tras cada pausa), pero no parcial en vivo.
5. **El calentamiento de 20 s de la primera prueba no se repitió:** la primera llamada en frío ahora cuesta 0,45 s.
   Era una compilación única de kernels de CUDA que quedó en caché.
6. **Despertar el motor con turbo en la 5080 cuesta ≈ 3–4 s** (arranque de Python + carga de 2,2 s + 0,45 s de
   calentamiento), y en ese tiempo el audio ya se está guardando en el búfer.
7. **Liberar exige cerrar el proceso:** borrar el modelo deja ≈ 240 MiB (contexto CUDA); cerrar el proceso devuelve la
   5080 a su valor inicial.

### Corrida 2 · tus dictados con jerga técnica (2026-10-21)

Grabados con `grabar.py`: 10 frases tipo dictado (7–10 s) y 2 tomas libres (25 y 29 s). La toma `dictado_01` quedó
fuera: es una prueba de micrófono («hola, hola, probando…»), no la frase. Se puntúan 9 frases con 24 apariciones de
términos del glosario. Resultados en `datos\f0\resultados\20261021-064125-dictado\` (con `comparacion.md`, frase por
frase).

| Config | VRAM pico | Frase media (8,3 s) | Parcial 2 s | WER | CER | Jerga acertada |
|---|---|---|---|---|---|---|
| **v3-gpu-int8 + glosario** | 2191 MiB | 0,48 s | 0,16 s | **3,2 %** | **1,1 %** | **24/24** |
| v3-gpu-fp16 | 4122 MiB | 0,42 s | 0,14 s | 5,7 % | 2,1 % | 20/24 |
| v3-gpu-int8 | 2205 MiB | 0,47 s | 0,16 s | 7,6 % | 2,4 % | 19/24 |
| turbo-gpu-int8 + glosario | 1321 MiB | 0,16 s | 0,10 s | 9,5 % | 3,4 % | 21/24 |
| turbo-gpu-fp16 | 2397 MiB | 0,18 s | 0,10 s | 9,5 % | 2,9 % | 19/24 |
| turbo-gpu-int8 | 1342 MiB | 0,18 s | 0,09 s | 10,1 % | 3,1 % | 19/24 |
| turbo-cpu-8 (corrida con la toma 1) | 0 | ≈ 5 s | 5,4 s | ≈ turbo-gpu-int8 | | 18/24 |

En todas las corridas: Δ 5090 = 0 MiB y la 5080 vuelve a su valor inicial al cerrar el motor (±36 MiB).

Lectura:

1. **Con jerga, large-v3 comete un tercio de los errores de turbo** (3,2 % frente a 9,5 % con glosario). turbo falla
   donde más duele en tus dictados: «n8n» → «NHN», «a LM Studio» → «ALM Studio», «wavelet» → «webled», «del» → «nel».
2. **El glosario funciona muy bien con large-v3** (de 7,6 % a 3,2 % y 24/24 términos) y poco con turbo: con turbo
   recupera Claude, LM Studio y tests, pero sigue quitando tildes («Pasale», «titulo») y se inventa «configs».
3. **El glosario no ensucia el habla normal:** en las tomas libres no aparece ningún término del glosario que no se
   haya dicho, y mejora la puntuación (pone los puntos que faltaban). Lección: escribirlo como frase natural en español,
   con tildes y puntuación; la primera versión (lista sin tildes) le contagiaba a turbo la falta de tildes.
4. **El coste de large-v3 sigue siendo bajo:** 2,2 GB, ≈ 0,5 s por frase y ≈ 0,16 s por refresco del texto parcial
   (cabe refrescar cada ~400 ms). Despertar cuesta ≈ 5 s (4 s de carga), con el audio ya en el búfer.
5. **Advertencia sobre el glosario de la prueba:** contiene exactamente los términos de las frases, así que el 24/24 es
   optimista. En uso real el glosario será tu lista personal de términos habituales, que es justo para lo que sirve.
6. **La CPU varía con la carga del equipo:** en esta corrida turbo en CPU tardó ≈ 5 s por llamada (3,3–3,7 s en la
   corrida 1).

### Decisión

| Modo | Configuración | Por qué |
|---|---|---|
| GPU (5080), por defecto | `large-v3`, `int8_float16`, glosario en `initial_prompt`; beam 5 en el final y beam 1 en el parcial; un solo modelo | 3,2 % WER y 24/24 en jerga, 2,2 GB, 0,5 s por frase |
| CPU (respaldo) | mismo modelo en `int8`, 8 hilos, solo texto final | 0 VRAM; ≈ 5,5–6 s por frase |

- El modelo y el glosario son configuración, no código: el adaptador del motor admite turbo si algún día hace falta
  ahorrar 0,9 GB de VRAM o ir más rápido a costa de precisión.
- `large-v3` ya estaba en disco (compartido con el otro proyecto), así que **la decisión no necesita disco
  extra**. turbo se borró el 2026-10-21 para recuperar 1,51 GB; si algún día hace falta, `entorno.descargar_modelo`
  lo vuelve a bajar en ~3 min (las configuraciones turbo de `bench.py` se omiten solas mientras no esté).

### Para F1

- Glosario editable por el usuario (archivo de configuración), escrito como frase natural.
- Probar la convivencia con el TTS del otro proyecto en la 5080 cuando el supervisor ya vigile la VRAM libre.
