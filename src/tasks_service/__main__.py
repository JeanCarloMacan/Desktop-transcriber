"""Arranque de tasks-service: `python -m tasks_service` (o `tasks-service`)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_core.ajustes import AjustesTareas

    from tasks_service.gpu import VigiaGpu


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="tasks-service",
                                 description="Organizador de tareas: dictados de voice-core → tareas con LM Studio")
    ap.add_argument("--config", type=Path, default=None, help="TOML de configuración (defecto: config/voice-core.toml)")
    ap.add_argument("--puerto", type=int, default=None)
    return ap.parse_args()


def main() -> int:
    a = _args()
    from voice_core import ajustes as mod_ajustes

    aj = mod_ajustes.cargar(a.config)
    if a.puerto:
        aj.tareas.puerto = a.puerto
    aj.carpeta_datos.mkdir(parents=True, exist_ok=True)
    salidas: list[logging.Handler] = [logging.FileHandler(aj.carpeta_datos / "tasks-service.log", encoding="utf-8")]
    if sys.stderr is not None:
        salidas.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=salidas)
    log = logging.getLogger("tasks_service")

    import uvicorn
    from voice_core.bus import Bus

    from tasks_service.almacen import AlmacenTareas
    from tasks_service.api import ServiciosTareas, crear_app
    from tasks_service.contratos import EVENTO_TAREAS, EVENTOS_EFIMEROS
    from tasks_service.llm import ClienteLlm
    from tasks_service.organizador import Organizador
    from tasks_service.voz import ClienteVoiceCore

    almacen = AlmacenTareas(aj.carpeta_datos / "tareas.db")
    bus = Bus(almacen, EVENTO_TAREAS, EVENTOS_EFIMEROS)
    t = aj.tareas
    llm = ClienteLlm(t.lmstudio_url, t.modelo, t.ttl_s, t.espera_s, parametros=t.parametros)
    vigia = _vigia_gpu(t, log)
    organizador = Organizador(t, almacen, bus, llm, ClienteVoiceCore(aj.url_voice_core), gpu=vigia)

    servidor: uvicorn.Server | None = None

    def pedir_cierre() -> None:
        log.info("Cierre pedido por la API")
        if servidor is not None:
            servidor.should_exit = True

    app = crear_app(ServiciosTareas(almacen, bus, organizador, llm, al_pedir_cierre=pedir_cierre))
    descarga = "al vaciarse la cola" if t.descargar_al_terminar else f"tras {t.ttl_s} s sin uso"
    log.info("tasks-service en %s · modelo %s en %s (se descarga %s) · categorías: %s",
             aj.url_tareas, t.modelo, t.lmstudio_url, descarga, ", ".join(organizador.categorias))
    servidor = uvicorn.Server(uvicorn.Config(app, host=aj.servidor.host, port=t.puerto, log_level="warning",
                                             log_config=None, timeout_graceful_shutdown=3))
    servidor.run()
    almacen.cerrar()
    log.info("tasks-service detenido")
    return 0


def _vigia_gpu(t: AjustesTareas, log: logging.Logger) -> VigiaGpu | None:
    """Salvaguarda de la GPU de LM Studio (`[tareas] gpu`); None si no se configura o no se encuentra."""
    if not t.gpu.strip():
        log.info("GPU de LM Studio sin vigilar ([tareas] gpu vacío)")
        return None
    from voice_core.gpu import resolver_gpu

    from tasks_service.gpu import VigiaGpu

    uuid = resolver_gpu(t.gpu)
    if uuid is None:
        log.warning("No se encontró la GPU «%s» de [tareas] gpu: el modelo se cargará sin comprobarla", t.gpu)
        return None
    vigia = VigiaGpu(uuid, t.gpu_vram_max_mib, t.gpu_uso_max_pct)
    log.info("GPU de LM Studio vigilada: %s (ocupada con más de %d MiB o %d %% de cómputo)",
             vigia.nombre() or uuid, t.gpu_vram_max_mib, t.gpu_uso_max_pct)
    return vigia


if __name__ == "__main__":
    sys.exit(main())
