"""Máquina de estados del motor: dispositivo, búfer de arranque, apagado automático y fallos del worker."""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from voice_core.ajustes import Ajustes
from voice_core.almacen import Almacen
from voice_core.bus import Bus
from voice_core.motor import Motor, MotorOcupado, MotorPausado, SinEspacioGpu

from conftest import FabricaFalsa, GpuFalsa, Reloj

pytestmark = pytest.mark.anyio


def crear(aj: Ajustes, libre: int = 12000, modo: str | None = None) -> tuple[Motor, FabricaFalsa, GpuFalsa, Reloj, Bus]:
    if modo:
        aj.motor.modo = modo  # type: ignore[assignment]
    bus = Bus(Almacen(":memory:"))
    fab, gpu, reloj = FabricaFalsa(), GpuFalsa(libre=libre), Reloj()
    motor = Motor(aj, bus, gpu, fab, reloj)
    motor.vincular(asyncio.get_running_loop())
    return motor, fab, gpu, reloj, bus


async def listo(motor: Motor, fab: FabricaFalsa) -> None:
    fab.ultimo.listo()
    await asyncio.sleep(0)


async def test_motor_usa_la_5080_si_hay_vram(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj)
    assert motor.encender() == "cuda"
    assert motor.estado == "arrancando"
    assert fab.ultimo.cfg["gpu_uuid"] == aj.gpu_uuid == "GPU-prueba"
    assert fab.ultimo.cfg["segmentacion"]["parciales"] is True
    await listo(motor, fab)
    assert motor.estado == "en_espera"


async def test_motor_auto_cae_a_cpu_sin_vram(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj, libre=1000)
    assert motor.encender() == "cpu"
    assert fab.ultimo.cfg["compute"] == "int8"
    assert fab.ultimo.cfg["segmentacion"]["parciales"] is False


async def test_motor_gpu_forzado_sin_espacio_falla(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj, libre=1000, modo="gpu")
    with pytest.raises(SinEspacioGpu):
        motor.encender()
    assert fab.creados == [] and motor.estado == "apagado"


async def test_sin_gpu_permitida_auto_usa_cpu_y_gpu_forzado_falla(aj: Ajustes) -> None:
    aj.motor.gpu = "4090"  # no coincide con ninguna GPU
    aj._gpu_resuelta, aj._uuid_gpu = True, None
    motor, fab, *_ = crear(aj)
    assert motor.encender() == "cpu"
    await motor.apagar("prueba")
    motor.modo = "gpu"
    with pytest.raises(SinEspacioGpu, match="4090"):
        motor.encender()


async def test_motor_pausado_rechaza_sesiones(aj: Ajustes) -> None:
    motor, *_ = crear(aj, modo="pausado")
    with pytest.raises(MotorPausado):
        motor.encender()


async def test_motor_encola_audio_hasta_estar_listo(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj)
    motor.encender()
    motor.iniciar_sesion()
    for _ in range(3):
        motor.enviar("audio", np.zeros(1600, dtype=np.float32))
    assert fab.ultimo.recibidos == []
    await listo(motor, fab)
    assert fab.ultimo.recibidos == ["reiniciar", "audio", "audio", "audio"]
    assert motor.estado == "dictando"


async def test_motor_se_apaga_por_inactividad_sin_reposo(aj: Ajustes) -> None:
    aj.motor.reposo_min = 0
    motor, fab, _, reloj, _ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    motor.iniciar_sesion()
    motor.sesion_cerrada()
    reloj.t += 30
    await motor.revisar()
    assert motor.estado == "en_espera"
    reloj.t += 31
    await motor.revisar()
    assert motor.estado == "apagado" and fab.ultimo.cerrado


async def test_motor_pasa_a_reposo_en_ram_y_luego_se_apaga(aj: Ajustes) -> None:
    """inactividad_min = 1 y reposo_min = 30: a los 61 s el modelo va a RAM; a los 31 min, apagado del todo."""
    motor, fab, _, reloj, _ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    reloj.t += 61
    assert motor.estado_out().rest_in_s == 0
    await motor.revisar()
    await asyncio.sleep(0)
    assert motor.estado == "en_reposo" and fab.ultimo.recibidos == ["dormir"] and not fab.ultimo.cerrado
    await motor.revisar()  # no se vuelve a pedir dormir
    assert fab.ultimo.recibidos == ["dormir"]
    assert motor.estado_out().auto_off_in_s == pytest.approx(30 * 60, abs=1)
    reloj.t += 30 * 60
    await motor.revisar()
    assert motor.estado == "apagado" and fab.ultimo.cerrado


async def test_motor_despierta_desde_reposo_al_dictar(aj: Ajustes) -> None:
    motor, fab, _, reloj, bus = crear(aj)
    motor.encender()
    await listo(motor, fab)
    reloj.t += 61
    await motor.revisar()
    await asyncio.sleep(0)
    assert motor.encender() == "cuda" and len(fab.creados) == 1  # mismo worker: no se relanza
    motor.iniciar_sesion()
    assert fab.ultimo.recibidos[-2:] == ["despertar", "reiniciar"]
    await asyncio.sleep(0)
    assert motor.estado == "dictando"
    motivos = [e.reason for e in bus.desde(0, 100) if e.type == "engine.state"]
    assert any("RAM a la 5080" in m for m in motivos) and any("0.35 s" in m for m in motivos)


async def test_dormido_tardio_no_pisa_un_dictado_en_curso(aj: Ajustes) -> None:
    motor, fab, _, reloj, _ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    motor.iniciar_sesion()
    motor._al_mensaje("dormido", {"s": 0.6})  # llega tarde, con la sesión ya empezada
    assert motor.estado == "dictando"


async def test_en_cpu_no_hay_reposo(aj: Ajustes) -> None:
    motor, fab, _, reloj, _ = crear(aj, modo="cpu")
    motor.encender()
    await listo(motor, fab)
    reloj.t += 61
    await motor.revisar()
    assert motor.estado == "apagado" and "dormir" not in fab.ultimo.recibidos


async def test_en_reposo_cede_la_gpu_si_falta_vram(aj: Ajustes) -> None:
    motor, fab, gpu, reloj, _ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    reloj.t += 61
    await motor.revisar()
    await asyncio.sleep(0)
    gpu.libre = 300
    await motor.revisar()
    assert motor.estado == "apagado"


async def test_motor_cede_la_gpu_si_baja_la_vram_libre(aj: Ajustes) -> None:
    motor, fab, gpu, *_ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    gpu.libre = 500
    await motor.revisar()
    assert motor.estado == "apagado" and fab.ultimo.cerrado


async def test_motor_nunca_se_apaga_dictando(aj: Ajustes) -> None:
    motor, fab, gpu, reloj, _ = crear(aj)
    motor.encender()
    motor.iniciar_sesion()
    await listo(motor, fab)
    reloj.t += 3600
    gpu.libre = 100
    await motor.revisar()
    assert motor.estado == "dictando"
    with pytest.raises(MotorOcupado):
        await motor.apagar("prueba")


async def test_motor_reintenta_en_cpu_si_la_gpu_falla_al_cargar(aj: Ajustes) -> None:
    motor, fab, _, _, bus = crear(aj)
    motor.encender()
    motor.iniciar_sesion()
    motor.enviar("audio", np.zeros(1600, dtype=np.float32))
    fab.ultimo.morir()
    await asyncio.sleep(0)
    assert len(fab.creados) == 2 and fab.ultimo.cfg["dispositivo"] == "cpu"
    await listo(motor, fab)
    assert fab.ultimo.recibidos == ["reiniciar", "audio"]
    assert any(e.type == "engine.error" for e in bus.desde(0, 100))


async def test_motor_avisa_si_el_worker_muere_en_sesion(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj)
    perdidos: list[str] = []
    motor.al_evento = lambda tipo, datos: perdidos.append(tipo)
    motor.encender()
    await listo(motor, fab)
    motor.iniciar_sesion()
    fab.ultimo.morir()
    await asyncio.sleep(0)
    assert motor.estado == "apagado" and perdidos == ["perdido"]


async def test_motor_cambiar_a_cpu_apaga_el_worker_en_gpu(aj: Ajustes) -> None:
    motor, fab, *_ = crear(aj)
    motor.encender()
    await listo(motor, fab)
    await motor.cambiar_modo("cpu")
    assert motor.estado == "apagado" and fab.ultimo.cerrado
    assert motor.encender() == "cpu"


async def test_motor_publica_cada_cambio_de_estado(aj: Ajustes) -> None:
    motor, fab, _, _, bus = crear(aj)
    motor.encender()
    await listo(motor, fab)
    await motor.apagar("prueba")
    estados = [e.state for e in bus.desde(0, 100) if e.type == "engine.state"]
    assert estados == ["arrancando", "en_espera", "apagado"]
