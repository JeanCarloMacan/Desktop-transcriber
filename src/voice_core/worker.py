"""stt-worker: proceso hijo que carga faster-whisper y transcribe; y su manejador en el supervisor.

Protocolo (tuplas por dos pipes simplex):
  supervisor → worker: ("audio", ndarray float32 16 kHz) · ("reiniciar", None) · ("vaciar", None) · ("salir", None) ·
                       ("dormir", None) · ("despertar", None)
  worker → supervisor: ("listo", {...}) · ("parcial", {texto}) · ("final", {texto, t0, t1}) · ("vaciado", {}) ·
                       ("dormido", {s}) · ("error", {mensaje})
«dormir» pasa el modelo a la RAM (la GPU queda solo con el contexto CUDA, ≈ 250 MB) y «despertar» lo devuelve
a la GPU en ≈ 0,4 s; si llega audio con el modelo dormido, el worker lo despierta antes de transcribir.
El manejador añade ("murio", {codigo}) si el proceso termina sin que se le pidiera, o ("cerrado", {}) si se le pidió.

Este módulo se importa en el supervisor: nada de CUDA a nivel de módulo.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

Mensaje = tuple[str, Any]
NO_VOZ_PROB = 0.6  # umbrales por defecto de Whisper (no_speech_threshold y logprob_threshold)
NO_VOZ_LOGPROB = -1.0


# ------------------------------------------------------------------ lado del worker (proceso hijo)

class VadSilero:
    """Silero VAD en streaming (una ventana de 512 muestras por llamada), sobre la sesión ONNX de faster-whisper."""

    CONTEXTO = 64

    def __init__(self, sesion: Any) -> None:
        self._sesion = sesion
        self.reiniciar()

    def reiniciar(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(self.CONTEXTO, dtype=np.float32)

    def __call__(self, ventana: np.ndarray) -> float:
        x = np.concatenate([self._ctx, ventana]).astype(np.float32)[None, :]
        salida, self._h, self._c = self._sesion.run(None, {"input": x, "h": self._h, "c": self._c})
        self._ctx = ventana[-self.CONTEXTO:]
        return float(np.asarray(salida).reshape(-1)[0])


def worker_main(entrada: Connection, salida: Connection, cfg: dict[str, Any]) -> None:
    # Antes de cualquier import de CUDA: el proceso solo verá la 5080 (o ninguna GPU en CPU).
    os.environ["CUDA_VISIBLE_DEVICES"] = cfg["gpu_uuid"] if cfg["dispositivo"] == "cuda" else "-1"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s stt-worker %(levelname)s %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    try:
        _ejecutar(entrada, salida, cfg)
    except Exception as e:
        log.exception("El worker falló")
        try:
            salida.send(("error", {"mensaje": f"{type(e).__name__}: {e}"}))
        except OSError:
            pass


def _recibir(entrada: Connection, bandeja: queue.Queue[Mensaje]) -> None:
    while True:
        try:
            bandeja.put(entrada.recv())
        except (EOFError, OSError):
            bandeja.put(("salir", None))
            return


def _ejecutar(entrada: Connection, salida: Connection, cfg: dict[str, Any]) -> None:
    from voice_core.gpu import preparar_cuda
    from voice_core.segmentador import SR, ParamsSegmentacion, Segmentador

    preparar_cuda([Path(d) for d in cfg["cuda_dll_dirs"]])
    import ctranslate2
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_vad_model

    if cfg["dispositivo"] == "cuda" and (n := ctranslate2.get_cuda_device_count()) != 1:
        raise RuntimeError(f"Se esperaba exactamente 1 GPU visible (la 5080) y hay {n}")

    t = time.monotonic()
    modelo = WhisperModel(cfg["modelo_path"], device=cfg["dispositivo"], device_index=0, compute_type=cfg["compute"],
                          cpu_threads=cfg["hilos"] if cfg["dispositivo"] == "cpu" else 0)

    def transcribir(audio: np.ndarray, final: bool) -> str:
        segs, _ = modelo.transcribe(audio, language=cfg["idioma"],
                                    beam_size=cfg["beam_final"] if final else cfg["beam_parcial"],
                                    initial_prompt=cfg["glosario"], condition_on_previous_text=False,
                                    vad_filter=False, without_timestamps=True)
        # Criterio de Whisper para «aquí no se habló»: se descarta lo que el propio modelo marca como no-voz.
        return " ".join(s.text.strip() for s in segs
                        if not (s.no_speech_prob > NO_VOZ_PROB and s.avg_logprob < NO_VOZ_LOGPROB)).strip()

    ruido = (np.random.default_rng(0).standard_normal(SR) * 0.003).astype(np.float32)
    transcribir(ruido, False)  # calentamiento: la primera llamada paga la inicialización de kernels
    carga_s = time.monotonic() - t
    segmentador = Segmentador(ParamsSegmentacion(**cfg["segmentacion"]), VadSilero(get_vad_model().session),
                              transcribir)
    salida.send(("listo", {"dispositivo": cfg["dispositivo"], "carga_s": round(carga_s, 2)}))
    log.info("Listo en %.1f s (%s, %s)", carga_s, cfg["dispositivo"], cfg["compute"])

    dormido = False

    def despertar() -> None:
        nonlocal dormido
        if dormido:
            t = time.monotonic()
            modelo.model.load_model()
            dormido = False
            salida.send(("listo", {"dispositivo": cfg["dispositivo"], "carga_s": round(time.monotonic() - t, 2),
                                   "despertado": True}))

    bandeja: queue.Queue[Mensaje] = queue.Queue()
    threading.Thread(target=_recibir, args=(entrada, bandeja), daemon=True, name="recepcion").start()
    while True:
        lote = [bandeja.get()]
        while True:  # junta lo acumulado mientras se transcribía: el audio atrasado se procesa de una vez
            try:
                lote.append(bandeja.get_nowait())
            except queue.Empty:
                break
        for tipo, datos in _agrupar_audio(lote):
            if tipo == "audio":
                despertar()
                for evento in segmentador.alimentar(datos):
                    salida.send(evento)
            elif tipo == "reiniciar":
                segmentador.reiniciar()
            elif tipo == "vaciar":
                despertar()
                for evento in segmentador.vaciar():
                    salida.send(evento)
                salida.send(("vaciado", {}))
            elif tipo == "dormir" and not dormido and cfg["dispositivo"] == "cuda":
                t = time.monotonic()
                modelo.model.unload_model(to_cpu=True)
                dormido = True
                salida.send(("dormido", {"s": round(time.monotonic() - t, 2)}))
            elif tipo == "despertar":
                despertar()
            elif tipo == "salir":
                log.info("Cierre pedido")
                return


def _agrupar_audio(lote: list[Mensaje]) -> list[Mensaje]:
    agrupado: list[Mensaje] = []
    for tipo, datos in lote:
        if tipo == "audio" and agrupado and agrupado[-1][0] == "audio":
            agrupado[-1] = ("audio", np.concatenate([agrupado[-1][1], datos]))
        else:
            agrupado.append((tipo, datos))
    return agrupado


# ------------------------------------------------------------------ lado del supervisor

class ProcesoWorker:
    """Lanza el worker con `spawn` y conversa con él por dos pipes; los mensajes llegan a `al_mensaje` desde un hilo."""

    def __init__(self, cfg: dict[str, Any], al_mensaje: Callable[[str, dict], None]) -> None:
        ctx = mp.get_context("spawn")
        hacia_r, hacia_w = ctx.Pipe(duplex=False)
        desde_r, desde_w = ctx.Pipe(duplex=False)
        self._proc = ctx.Process(target=worker_main, args=(hacia_r, desde_w, cfg), daemon=True, name="stt-worker")
        self._proc.start()
        hacia_r.close()
        desde_w.close()  # sin esto, la muerte del hijo nunca daría EOF en el lector
        self._hacia, self._desde = hacia_w, desde_r
        self._al_mensaje = al_mensaje
        self._cerrando = False
        self._cola: queue.Queue[Mensaje | None] = queue.Queue()
        threading.Thread(target=self._emitir, daemon=True, name="worker-emisor").start()
        threading.Thread(target=self._leer, daemon=True, name="worker-lector").start()

    @property
    def vivo(self) -> bool:
        return self._proc.is_alive()

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    def enviar(self, tipo: str, datos: Any = None) -> None:
        self._cola.put((tipo, datos))

    def _emitir(self) -> None:
        while (msg := self._cola.get()) is not None:
            try:
                self._hacia.send(msg)
            except (OSError, ValueError):
                return

    def _leer(self) -> None:
        while True:
            try:
                tipo, datos = self._desde.recv()
            except (EOFError, OSError):
                self._proc.join(timeout=5)
                if self._cerrando:
                    self._al_mensaje("cerrado", {})
                else:
                    self._al_mensaje("murio", {"codigo": self._proc.exitcode})
                return
            self._al_mensaje(tipo, datos)

    def cerrar(self, espera_s: float = 5.0) -> None:
        """Pide salir y, si no sale a tiempo, lo mata. Bloquea: llamar fuera del bucle asyncio."""
        self._cerrando = True
        self.enviar("salir")
        self._proc.join(timeout=espera_s)
        if self._proc.is_alive():
            log.warning("El worker no salió en %.0f s: se termina", espera_s)
            self._proc.terminate()
            self._proc.join(timeout=5)
        self._cola.put(None)
        self._hacia.close()
