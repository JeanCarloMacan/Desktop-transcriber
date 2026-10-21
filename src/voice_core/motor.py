"""Motor: decide dispositivo, lanza y cierra el stt-worker y lleva la máquina de estados.

apagado ──encender──► arrancando ──listo──► dictando ⇄ en_espera ──(inactividad_min, solo GPU)──► en_reposo
en_reposo (modelo en RAM, ≈ 250 MB en la 5080) ──sesión──► arrancando ──despierto (≈ 0,4 s)──► dictando
en_espera / en_reposo ──(reposo_min agotado · VRAM baja · liberar · modo)──► apagado
Todo método se llama desde el hilo del bucle asyncio; los mensajes del worker llegan con call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from voice_core.ajustes import Ajustes
from voice_core.bus import Bus, ahora
from voice_core.contratos import Dispositivo, EngineError, EngineState, EngineStatusOut, EstadoMotor, ModoMotor
from voice_core.worker import ProcesoWorker

log = logging.getLogger(__name__)


class MotorPausado(Exception):
    pass


class SinEspacioGpu(Exception):
    pass


class MotorOcupado(Exception):
    pass


class InfoGpu(Protocol):
    def memoria(self, uuid: str) -> tuple[int, int] | None: ...


class Worker(Protocol):
    vivo: bool

    def enviar(self, tipo: str, datos: Any = None) -> None: ...

    def cerrar(self, espera_s: float = 5.0) -> None: ...


FabricaWorker = Callable[[dict[str, Any], Callable[[str, dict], None]], Worker]


class Motor:
    def __init__(self, aj: Ajustes, bus: Bus, gpu: InfoGpu, fabrica: FabricaWorker = ProcesoWorker,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self._aj = aj
        self._bus = bus
        self._gpu = gpu
        self._fabrica = fabrica
        self._reloj = reloj
        self.estado: EstadoMotor = "apagado"
        self.modo: ModoMotor = aj.motor.modo
        self.dispositivo: Dispositivo | None = None
        self._worker: Worker | None = None
        self._listo = False
        self._pendientes: list[tuple[str, Any]] = []
        self._sesion_activa = False
        self._durmiendo = False  # se pidió «dormir» y aún no llegó «dormido»
        self._reintento_cpu = False
        self._t_arranque = 0.0
        self._ultimo_uso = reloj()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.al_evento: Callable[[str, dict], None] = lambda tipo, datos: None

    def vincular(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ------------------------------------------------------------ ciclo de vida

    def encender(self) -> Dispositivo:
        """Arranca el worker si no hay uno vivo. No espera a que cargue: el audio se encola mientras tanto."""
        if self.modo == "pausado":
            raise MotorPausado("El motor está en pausa")
        if self._worker is not None and self.dispositivo is not None:
            return self.dispositivo
        self._lanzar(self._elegir_dispositivo())
        self._reintento_cpu = False
        return self.dispositivo  # type: ignore[return-value]

    def _elegir_dispositivo(self) -> Dispositivo:
        if self.modo == "cpu":
            return "cpu"
        uuid = self._aj.gpu_uuid
        if uuid is None:
            if self.modo == "gpu":
                raise SinEspacioGpu(f"Ninguna GPU permitida coincide con «{self._aj.motor.gpu}» (config: motor.gpu)")
            return "cpu"
        mem = self._gpu.memoria(uuid)
        libre = mem[1] if mem else None
        if libre is not None and libre >= self._aj.motor.vram_minima_mib:
            return "cuda"
        if self.modo == "gpu":
            raise SinEspacioGpu(f"La 5080 tiene {libre} MiB libres y hacen falta {self._aj.motor.vram_minima_mib}")
        return "cpu"

    def _lanzar(self, dispositivo: Dispositivo) -> None:
        self.dispositivo = dispositivo
        self._listo = False
        self._t_arranque = self._reloj()
        self._worker = self._fabrica(self._cfg_worker(dispositivo), self._desde_hilo)
        self._cambiar("arrancando", f"cargando {self._aj.motor.modelo} en {dispositivo}")

    def _cfg_worker(self, dispositivo: Dispositivo) -> dict[str, Any]:
        m, s = self._aj.motor, self._aj.segmentacion
        return {
            "dispositivo": dispositivo,
            "gpu_uuid": self._aj.gpu_uuid or "",
            "cuda_dll_dirs": [str(d) for d in self._aj.dirs_dll_cuda],
            "modelo_path": str(self._aj.ruta_modelo),
            "compute": m.compute_gpu if dispositivo == "cuda" else m.compute_cpu,
            "hilos": m.hilos_cpu,
            "idioma": m.idioma,
            "glosario": self._aj.texto_glosario,
            "beam_final": m.beam_final,
            "beam_parcial": m.beam_parcial,
            "segmentacion": {**s.model_dump(), "parciales": dispositivo == "cuda"},
        }

    async def apagar(self, motivo: str, forzar: bool = False) -> None:
        if self._sesion_activa and not forzar:
            raise MotorOcupado("Hay un dictado en curso")
        worker, self._worker = self._worker, None
        self._listo = False
        self._durmiendo = False
        self._pendientes.clear()
        self.dispositivo = None
        if worker is not None:
            await asyncio.get_running_loop().run_in_executor(None, worker.cerrar)
        if self.estado != "apagado":
            self._cambiar("apagado", motivo)

    # ------------------------------------------------------------ sesión

    def enviar(self, tipo: str, datos: Any = None) -> None:
        if self._worker is not None and self._listo:
            self._worker.enviar(tipo, datos)
        else:
            self._pendientes.append((tipo, datos))

    def iniciar_sesion(self) -> None:
        self._sesion_activa = True
        self._durmiendo = False
        if self.estado == "en_reposo":
            self.enviar("despertar")  # el worker lo procesa antes que el audio que llegue después
            self._cambiar("arrancando", "devolviendo el modelo de la RAM a la 5080")
        self.enviar("reiniciar")
        if self.estado == "en_espera":
            self._cambiar("dictando", "sesión iniciada")

    def terminar_sesion(self) -> None:
        self.enviar("vaciar")

    def sesion_cerrada(self) -> None:
        self._sesion_activa = False
        self._ultimo_uso = self._reloj()
        if self.estado == "dictando":
            self._cambiar("en_espera", "sesión terminada")

    # ------------------------------------------------------------ mensajes del worker

    def _desde_hilo(self, tipo: str, datos: dict) -> None:
        if self._loop is None:
            self._al_mensaje(tipo, datos)
        else:
            self._loop.call_soon_threadsafe(self._al_mensaje, tipo, datos)

    def _al_mensaje(self, tipo: str, datos: dict) -> None:
        if tipo == "listo" and datos.get("despertado"):
            self._cambiar("dictando" if self._sesion_activa else "en_espera",
                          f"modelo de vuelta en la 5080 en {datos.get('carga_s', 0):.2f} s")
        elif tipo == "dormido":
            self._durmiendo = False
            if not self._sesion_activa:  # si ya empezó otro dictado, el worker se despierta solo con el audio
                self._cambiar("en_reposo", f"modelo en RAM en {datos.get('s', 0):.1f} s; la 5080 queda con ≈ 250 MB")
        elif tipo == "listo":
            self._listo = True
            pendientes, self._pendientes = self._pendientes, []
            for t, d in pendientes:
                self._worker.enviar(t, d)  # type: ignore[union-attr]
            self._cambiar("dictando" if self._sesion_activa else "en_espera",
                          f"modelo listo en {datos.get('carga_s', 0):.1f} s")
        elif tipo in ("parcial", "final", "vaciado"):
            self.al_evento(tipo, datos)
        elif tipo == "error":
            log.error("Error del worker: %s", datos.get("mensaje"))
            self._bus.publicar(EngineError(seq=None, ts=ahora(), message=datos.get("mensaje", "error del worker")))
        elif tipo == "murio":
            self._al_morir(datos.get("codigo"))

    def _al_morir(self, codigo: int | None) -> None:
        arrancando_en_gpu = self.estado == "arrancando" and self.dispositivo == "cuda"
        self._worker = None
        self._listo = False
        if arrancando_en_gpu and self.modo == "auto" and not self._reintento_cpu:
            # La GPU falló al cargar: se reintenta en CPU conservando el audio ya encolado.
            self._reintento_cpu = True
            self._bus.publicar(EngineError(seq=None, ts=ahora(),
                                           message=f"El worker en GPU terminó (código {codigo}); se reintenta en CPU"))
            pendientes = self._pendientes
            self._lanzar("cpu")
            self._pendientes = pendientes
            return
        self._pendientes.clear()
        self.dispositivo = None
        self._bus.publicar(EngineError(seq=None, ts=ahora(), message=f"El worker terminó inesperadamente (código {codigo})"))
        self._cambiar("apagado", "el worker terminó")
        if self._sesion_activa:
            self.al_evento("perdido", {})

    # ------------------------------------------------------------ vigilancia

    async def vigilar(self, cada_s: float = 5.0) -> None:
        while True:
            await asyncio.sleep(cada_s)
            try:
                await self.revisar()
            except Exception:
                log.exception("Fallo al revisar el motor")

    async def revisar(self) -> None:
        if self.estado == "arrancando" and self._reloj() - self._t_arranque > self._aj.motor.arranque_max_s:
            self._bus.publicar(EngineError(seq=None, ts=ahora(), message="El modelo no terminó de cargar a tiempo"))
            await self.apagar("arranque demasiado lento", forzar=True)
            if self._sesion_activa:
                self.al_evento("perdido", {})
            return
        if self.estado not in ("en_espera", "en_reposo"):
            return
        m = self._aj.motor
        inactivo = self._reloj() - self._ultimo_uso
        if self.estado == "en_reposo" and inactivo >= (m.inactividad_min + m.reposo_min) * 60:
            await self.apagar(f"{m.reposo_min:g} min en reposo sin uso")
            return
        if self.estado == "en_espera" and inactivo >= m.inactividad_min * 60:
            if self._con_reposo():
                if not self._durmiendo:
                    self._durmiendo = True
                    self.enviar("dormir")
            else:
                await self.apagar(f"{m.inactividad_min:g} min sin uso")
            return
        if self.dispositivo == "cuda" and self._aj.gpu_uuid:
            mem = self._gpu.memoria(self._aj.gpu_uuid)
            if mem is not None and mem[1] < m.vram_ceder_mib:
                await self.apagar(f"la 5080 quedó con {mem[1]} MiB libres: se cede la GPU")

    def _con_reposo(self) -> bool:
        return self.dispositivo == "cuda" and self._aj.motor.reposo_min > 0

    async def cambiar_modo(self, modo: ModoMotor) -> None:
        anterior, self.modo = self.modo, modo
        incompatible = (modo == "pausado" or (modo == "cpu" and self.dispositivo == "cuda")
                        or (modo == "gpu" and self.dispositivo == "cpu"))
        if self._worker is not None and incompatible and not self._sesion_activa:
            await self.apagar(f"modo {anterior} → {modo}")
        else:
            self._cambiar(self.estado, f"modo {anterior} → {modo}")

    # ------------------------------------------------------------ estado

    def _cambiar(self, estado: EstadoMotor, motivo: str) -> None:
        self.estado = estado
        log.info("Motor: %s (%s)", estado, motivo)
        self._bus.publicar(EngineState(seq=None, ts=ahora(), state=estado, mode=self.modo, device=self.dispositivo,
                                       reason=motivo))

    def estado_out(self) -> EngineStatusOut:
        m = self._aj.motor
        mem = self._gpu.memoria(self._aj.gpu_uuid) if self._aj.gpu_uuid else None
        inactivo = self._reloj() - self._ultimo_uso if self.estado in ("en_espera", "en_reposo") else None
        reposo = m.reposo_min if self._con_reposo() else 0.0
        a_reposo = (max(0.0, m.inactividad_min * 60 - inactivo)
                    if inactivo is not None and self.estado == "en_espera" and reposo else None)
        a_apagado = max(0.0, (m.inactividad_min + reposo) * 60 - inactivo) if inactivo is not None else None
        return EngineStatusOut(state=self.estado, mode=self.modo, device=self.dispositivo, model=m.modelo,
                               idle_s=round(inactivo, 1) if inactivo is not None else None,
                               rest_in_s=round(a_reposo, 1) if a_reposo is not None else None,
                               auto_off_in_s=round(a_apagado, 1) if a_apagado is not None else None,
                               vram_used_mib=mem[0] if mem else None, vram_free_mib=mem[1] if mem else None)
