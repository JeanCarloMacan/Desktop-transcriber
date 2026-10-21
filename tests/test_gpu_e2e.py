"""Punta a punta con el modelo real en la 5080: worker de verdad, VAD de verdad y un dictado grabado en F0.

Se corre aparte:  .venv\\Scripts\\python -m pytest -m gpu -s
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from voice_core.ajustes import Ajustes, cargar
from voice_core.almacen import Almacen
from voice_core.audio import FuenteArchivo
from voice_core.bus import Bus
from voice_core.contratos import Evento
from voice_core.gpu import Nvml, listar_gpus
from voice_core.motor import Motor
from voice_core.sesiones import GestorSesiones

RAIZ = Path(__file__).resolve().parents[1]
DICTADO = RAIZ / "datos" / "f0" / "audios" / "dictado_08.wav"

pytestmark = [pytest.mark.gpu, pytest.mark.anyio,
              pytest.mark.skipif(not DICTADO.is_file(), reason="falta el dictado grabado en F0")]


def ajustes(tmp_path: Path, modo: str) -> Ajustes:
    aj = cargar(RAIZ / "config" / "voice-core.toml")
    aj.datos.carpeta = tmp_path / "datos"
    aj.motor.modo = modo  # type: ignore[assignment]
    return aj


async def dictar(aj: Ajustes, precalentar: bool, velocidad: float = 1.0) -> tuple[list[Evento], float]:
    """Con precalentar, el motor ya está en_espera al empezar (el caso normal); sin él, se mide el arranque en frío."""
    loop = asyncio.get_running_loop()
    almacen = Almacen(":memory:")
    bus = Bus(almacen)
    motor = Motor(aj, bus, Nvml())
    motor.vincular(loop)
    fuentes: list[FuenteArchivo] = []

    def fabrica(entregar):  # type: ignore[no-untyped-def]
        fuentes.append(FuenteArchivo(entregar, DICTADO, velocidad=velocidad))
        return fuentes[-1]

    sesiones = GestorSesiones(aj, almacen, bus, motor, fabrica)
    sesiones.vincular(loop)
    if precalentar:
        motor.encender()
        while motor.estado != "en_espera":
            await asyncio.sleep(0.1)
    cola = bus.suscribir()
    t = time.monotonic()
    await sesiones.iniciar("dictado")
    while not fuentes[0].terminado.is_set():
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)  # silencio para que el VAD cierre la frase
    await sesiones.detener()
    await sesiones.esperar_cierres()
    duracion = time.monotonic() - t
    await motor.apagar("fin de la prueba")
    eventos = []
    while not cola.empty():
        eventos.append(cola.get_nowait())
    return [e for e in eventos if e is not None], duracion


async def test_e2e_en_la_5080_transcribe_y_libera_la_vram(tmp_path: Path) -> None:
    nvml = Nvml()
    uuid = cargar().gpu_uuid
    assert uuid, "La configuración no permite ninguna GPU (motor.gpu)"
    otras = {u: nvml.memoria(u)[0] for u, _ in listar_gpus() if u != uuid}  # p. ej. la 5090 de los LLM
    v5080 = nvml.memoria(uuid)
    assert v5080
    eventos, duracion = await dictar(ajustes(tmp_path, "gpu"), precalentar=True)
    await asyncio.sleep(1.5)
    tipos = [e.type for e in eventos]
    final = next(e for e in eventos if e.type == "session.finalized")
    parciales = [e.text for e in eventos if e.type == "segment.partial"]
    print(f"\n[gpu] {duracion:.1f} s · {len(parciales)} parciales · primero: {parciales[:1]} · texto: {final.text}")
    assert "learning rate" in final.text and "checkpoint" in final.text
    assert len(parciales) >= 5
    assert not any("subtítulos" in p.lower() for p in parciales), parciales
    assert [e.state for e in eventos if e.type == "engine.state"][0] == "dictando"
    despues = nvml.memoria(uuid)
    # Otros programas pueden liberar memoria en la 5080 durante la prueba: se comprueba que no quede nada nuestro.
    assert despues[0] - v5080[0] <= 50, f"La 5080 no volvió a su VRAM inicial: {v5080[0]} → {despues[0]} MiB"
    for otra, antes in otras.items():  # el contexto CUDA ocuparía ≥ 200 MiB: 100 MiB distingue de otros programas
        assert abs(nvml.memoria(otra)[0] - antes) < 100, f"Otra GPU ({otra}) cambió de memoria"


async def test_e2e_arranque_en_frio_no_pierde_audio(tmp_path: Path) -> None:
    """Se empieza a hablar con el motor apagado: lo dicho mientras carga queda en el búfer y se transcribe."""
    eventos, duracion = await dictar(ajustes(tmp_path, "gpu"), precalentar=False, velocidad=2.0)
    estados = [e.state for e in eventos if e.type == "engine.state"]
    final = next(e for e in eventos if e.type == "session.finalized")
    print(f"\n[frío] {duracion:.1f} s · estados {estados} · texto: {final.text}")
    assert estados[:2] == ["arrancando", "dictando"]
    assert final.text.startswith("Sube un poco") and "checkpoint" in final.text


async def test_e2e_reposo_en_ram_libera_vram_y_despierta_rapido(tmp_path: Path) -> None:
    """Modelo cargado → pasa a RAM (la 5080 se queda con ≈ 250 MB) → un dictado lo despierta en < 2 s y transcribe."""
    aj = ajustes(tmp_path, "gpu")
    aj.motor.inactividad_min = 0.01  # 0,6 s para pasar a reposo
    nvml = Nvml()
    base = nvml.memoria(aj.gpu_uuid)[0]
    loop = asyncio.get_running_loop()
    almacen = Almacen(":memory:")
    bus = Bus(almacen)
    motor = Motor(aj, bus, nvml)
    motor.vincular(loop)
    fuentes: list[FuenteArchivo] = []

    def fabrica(entregar):  # type: ignore[no-untyped-def]
        fuentes.append(FuenteArchivo(entregar, DICTADO))
        return fuentes[-1]

    sesiones = GestorSesiones(aj, almacen, bus, motor, fabrica)
    sesiones.vincular(loop)
    motor.encender()
    while motor.estado != "en_espera":
        await asyncio.sleep(0.1)
    cargado = nvml.memoria(aj.gpu_uuid)[0] - base
    await asyncio.sleep(1)
    await motor.revisar()
    while motor.estado != "en_reposo":
        await asyncio.sleep(0.1)
    await asyncio.sleep(1)
    en_reposo = nvml.memoria(aj.gpu_uuid)[0] - base
    cola = bus.suscribir()
    t = time.monotonic()
    await sesiones.iniciar("dictado")
    while motor.estado != "dictando":
        await asyncio.sleep(0.02)
    despertar = time.monotonic() - t
    while not fuentes[0].terminado.is_set():
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.2)
    await sesiones.detener()
    await sesiones.esperar_cierres()
    await motor.apagar("fin de la prueba")
    eventos = []
    while not cola.empty():
        eventos.append(cola.get_nowait())
    final = next(e for e in eventos if e is not None and e.type == "session.finalized")
    print(f"\n[reposo] cargado +{cargado} MiB · en reposo +{en_reposo} MiB · despertar {despertar:.2f} s · "
          f"texto: {final.text}")
    assert cargado > 1500 and en_reposo < 600
    assert despertar < 2.0
    assert "learning rate" in final.text


async def test_e2e_en_cpu_solo_da_texto_final(tmp_path: Path) -> None:
    eventos, duracion = await dictar(ajustes(tmp_path, "cpu"), precalentar=False, velocidad=2.0)
    tipos = [e.type for e in eventos]
    final = next(e for e in eventos if e.type == "session.finalized")
    print(f"\n[cpu] {duracion:.1f} s · texto: {final.text}")
    assert "segment.partial" not in tipos
    assert "learning rate" in final.text
