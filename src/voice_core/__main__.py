"""Arranque de voice-core: `python -m voice_core` (o `voice-core`).

Los imports pesados van dentro de main(): el worker se lanza con `spawn` y reimporta este módulo.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="voice-core", description="Servicio local de voz a texto en tiempo real")
    ap.add_argument("--config", type=Path, default=None, help="TOML de configuración (defecto: config/voice-core.toml)")
    ap.add_argument("--puerto", type=int, default=None)
    ap.add_argument("--sin-hotkeys", action="store_true")
    ap.add_argument("--fuente-archivo", type=Path, default=None,
                    help="usa este WAV en lugar del micrófono en cada sesión (pruebas y demos)")
    ap.add_argument("--descargar-modelo", action="store_true",
                    help="descarga el modelo configurado (motor.modelo) a motor.carpeta_modelos y termina")
    return ap.parse_args()


def _descargar_modelo(aj: object) -> int:
    """Baja el modelo CTranslate2 de Hugging Face a una carpeta normal (sin symlinks: en Windows fallan sin permisos)."""
    import os

    from huggingface_hub import snapshot_download

    from voice_core.ajustes import REPOS_MODELOS

    log = logging.getLogger("voice_core")
    repo = REPOS_MODELOS.get(aj.motor.modelo)  # type: ignore[attr-defined]
    if repo is None:
        log.error("«%s» no es un modelo conocido (%s)", aj.motor.modelo, ", ".join(REPOS_MODELOS))  # type: ignore[attr-defined]
        return 2
    destino = aj.ruta_modelo  # type: ignore[attr-defined]
    destino.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    log.info("Descargando %s en %s (large-v3 ≈ 2,9 GB)…", repo, destino)
    snapshot_download(repo, local_dir=str(destino),
                      allow_patterns=["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json",
                                      "vocabulary.*"])
    log.info("Modelo listo en %s", destino)
    return 0


def _sonido(nombre: str) -> None:
    import winsound

    tipos = {"inicio": winsound.MB_ICONASTERISK, "liberado": winsound.MB_ICONEXCLAMATION}
    winsound.MessageBeep(tipos.get(nombre, winsound.MB_OK))


def main() -> int:
    a = _args()
    from voice_core import ajustes as mod_ajustes

    aj = mod_ajustes.cargar(a.config)
    if a.puerto:
        aj.servidor.puerto = a.puerto
    aj.carpeta_datos.mkdir(parents=True, exist_ok=True)
    salidas: list[logging.Handler] = [logging.FileHandler(aj.carpeta_datos / "voice-core.log", encoding="utf-8")]
    if sys.stderr is not None:  # con pythonw (sin consola) no hay stderr: solo el archivo
        salidas.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=salidas)
    log = logging.getLogger("voice_core")

    import uvicorn

    from voice_core.almacen import Almacen
    from voice_core.api import Servicios, crear_app
    from voice_core.audio import FuenteArchivo, FuenteMicrofono
    from voice_core.bus import Bus
    from voice_core.gpu import Nvml
    from voice_core.hotkeys import Hotkeys
    from voice_core.motor import Motor
    from voice_core.sesiones import GestorSesiones

    if a.descargar_modelo:
        return _descargar_modelo(aj)
    if not (aj.ruta_modelo / "model.bin").is_file():
        log.error("No está el modelo en %s. Descárgalo con: .venv\\Scripts\\voice-core --descargar-modelo",
                  aj.ruta_modelo)
        return 2
    almacen = Almacen(aj.carpeta_datos / "voice-core.db")
    bus = Bus(almacen)
    motor = Motor(aj, bus, Nvml())
    if a.fuente_archivo:
        def fabrica(entregar):  # type: ignore[no-untyped-def]
            return FuenteArchivo(entregar, a.fuente_archivo)
    else:
        def fabrica(entregar):  # type: ignore[no-untyped-def]
            return FuenteMicrofono(entregar, aj.audio.dispositivo)
    sesiones = GestorSesiones(aj, almacen, bus, motor, fabrica, _sonido if aj.hotkeys.sonidos else (lambda n: None))

    hotkeys: Hotkeys | None = None

    def al_arrancar(loop: asyncio.AbstractEventLoop) -> None:
        nonlocal hotkeys
        if a.sin_hotkeys:
            return

        def lanzar(crear_corrutina):  # type: ignore[no-untyped-def]
            def accion() -> None:
                futuro = asyncio.run_coroutine_threadsafe(crear_corrutina(), loop)
                futuro.add_done_callback(lambda f: f.exception() and log.warning("Hotkey: %s", f.exception()))
            return accion

        hotkeys = Hotkeys({
            aj.hotkeys.dictar: lanzar(lambda: sesiones.alternar("dictado")),
            aj.hotkeys.tarea: lanzar(lambda: sesiones.alternar("tarea")),
            aj.hotkeys.liberar: lanzar(lambda: sesiones.liberar(f"liberado con {aj.hotkeys.liberar}")),
        })
        hotkeys.iniciar()

    def al_parar() -> None:
        if hotkeys is not None:
            hotkeys.detener()

    servidor: uvicorn.Server | None = None

    def pedir_cierre() -> None:
        log.info("Cierre pedido por la API")
        if servidor is not None:
            servidor.should_exit = True

    app = crear_app(Servicios(aj, almacen, bus, motor, sesiones, al_arrancar, al_parar, pedir_cierre))
    log.info("voice-core en http://%s:%d · modelo %s · modo %s · tras cada dictado: %g min en la GPU, %g min en RAM "
             "y luego se apaga", aj.servidor.host, aj.servidor.puerto, aj.motor.modelo, aj.motor.modo,
             aj.motor.inactividad_min, aj.motor.reposo_min)
    if not a.sin_hotkeys:
        log.info("%s dictar · %s tarea · %s liberar la GPU · Ctrl+C en esta consola detiene el servicio",
                 aj.hotkeys.dictar, aj.hotkeys.tarea, aj.hotkeys.liberar)
    # log_config=None: uvicorn no instala sus propios manejadores y sus mensajes van a los nuestros (archivo y consola).
    # timeout_graceful_shutdown: con Ctrl+C, no esperar sin límite a que los clientes cierren sus WebSocket.
    servidor = uvicorn.Server(uvicorn.Config(app, host=aj.servidor.host, port=aj.servidor.puerto,
                                             log_level="warning", log_config=None, timeout_graceful_shutdown=3))
    servidor.run()
    almacen.cerrar()
    log.info("voice-core detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
