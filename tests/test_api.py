"""La API cumple el contrato de docs/F1_VOICE_CORE.md con worker, GPU y micrófono falsos."""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_core.ajustes import Ajustes
from voice_core.almacen import Almacen
from voice_core.api import Servicios, crear_app
from voice_core.bus import Bus
from voice_core.motor import Motor
from voice_core.sesiones import GestorSesiones

from conftest import FabricaFalsa, FuenteFalsa, GpuFalsa, esperar


@dataclass
class Entorno:
    c: TestClient
    sesiones: GestorSesiones
    fab: FabricaFalsa
    fuentes: list[FuenteFalsa]
    cierres: list[str]

    def hablar(self, trozos: int = 2) -> None:
        for _ in range(trozos):
            self.sesiones._desde_audio(np.full(1600, 0.1, dtype=np.float32))

    def sesion(self, id_: str) -> dict:
        return self.c.get(f"/sessions/{id_}").json()

    def dictar(self, modo: str = "dictado") -> dict:
        s = self.c.post("/sessions", json={"mode": modo}).json()
        self.hablar()
        esperar(lambda: self.fab.ultimo.muestras == 3200)
        self.c.post(f"/sessions/{s['id']}/stop")
        esperar(lambda: self.sesion(s["id"])["status"] == "final")
        return self.sesion(s["id"])


@pytest.fixture
def ent(aj: Ajustes) -> Iterator[Entorno]:
    almacen = Almacen(":memory:")
    bus = Bus(almacen)
    fab = FabricaFalsa(auto_listo=True)
    motor = Motor(aj, bus, GpuFalsa(), fab)
    fuentes: list[FuenteFalsa] = []

    def fabrica_fuente(entregar):  # type: ignore[no-untyped-def]
        fuentes.append(FuenteFalsa(entregar))
        return fuentes[-1]

    sesiones = GestorSesiones(aj, almacen, bus, motor, fabrica_fuente)
    cierres: list[str] = []
    with TestClient(crear_app(Servicios(aj, almacen, bus, motor, sesiones,
                                        al_pedir_cierre=lambda: cierres.append("cierre")))) as c:
        yield Entorno(c, sesiones, fab, fuentes, cierres)


def test_health_y_motor_apagado_al_arrancar(ent: Entorno) -> None:
    assert ent.c.get("/health").json()["status"] == "ok"
    motor = ent.c.get("/engine").json()
    assert motor["state"] == "apagado" and motor["device"] is None and motor["mode"] == "auto"


def test_dictado_completo_de_principio_a_fin(ent: Entorno, aj: Ajustes) -> None:
    r = ent.c.post("/sessions", json={"mode": "dictado"})
    assert r.status_code == 201
    s = r.json()
    assert s["status"] == "grabando" and s["device"] == "cuda" and ent.fuentes[-1].abierta
    esperar(lambda: ent.c.get("/engine").json()["state"] == "dictando")
    ent.hablar()
    esperar(lambda: ent.fab.ultimo.muestras == 3200)
    parada = ent.c.post(f"/sessions/{s['id']}/stop").json()
    assert parada["status"] in ("procesando", "final") and not ent.fuentes[-1].abierta
    esperar(lambda: ent.sesion(s["id"])["status"] == "final")
    detalle = ent.sesion(s["id"])
    assert detalle["text"] == "frase de 3200 muestras"
    assert detalle["segments"] == [{"index": 0, "t0": 0.0, "t1": 0.2, "text": "frase de 3200 muestras"}]
    assert detalle["audio_path"] and (aj.carpeta_datos / "sesiones" / s["id"] / "audio.wav").is_file()
    assert ent.c.get("/engine").json()["state"] == "en_espera"


def test_events_devuelve_los_persistentes_en_orden_y_sin_parciales(ent: Entorno) -> None:
    ent.dictar()
    eventos = ent.c.get("/events?after=0").json()
    tipos = [e["type"] for e in eventos]
    seqs = [e["seq"] for e in eventos]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert "segment.partial" not in tipos
    orden = [t for t in tipos if t in ("session.started", "session.stopped", "session.finalized")]
    assert orden == ["session.started", "session.stopped", "session.finalized"]  # micrófono cerrado, luego el texto
    assert "segment.final" in tipos
    corte = seqs[1]
    assert [e["seq"] for e in ent.c.get(f"/events?after={corte}").json()] == seqs[2:]


def test_websocket_reenvia_lo_guardado_y_sigue_en_vivo(ent: Entorno) -> None:
    previa = ent.dictar()
    with ent.c.websocket_connect("/ws?after=0") as ws:
        reenviados = []
        while True:
            ev = json.loads(ws.receive_text())
            reenviados.append(ev)
            if ev["type"] == "session.finalized":
                break
        assert reenviados[-1]["session_id"] == previa["id"]
        nueva = ent.c.post("/sessions", json={"mode": "tarea"}).json()
        ent.hablar(1)
        vistos = []
        while not {"session.started", "segment.partial"} <= {e["type"] for e in vistos}:
            vistos.append(json.loads(ws.receive_text()))
        parcial = next(e for e in vistos if e["type"] == "segment.partial")
        assert parcial["seq"] is None and parcial["session_id"] == nueva["id"]


def test_no_se_pueden_tener_dos_sesiones_a_la_vez(ent: Entorno) -> None:
    assert ent.c.post("/sessions", json={"mode": "dictado"}).status_code == 201
    r = ent.c.post("/sessions", json={"mode": "dictado"})
    assert r.status_code == 409 and "activa" in r.json()["detail"]


def test_liberar_falla_dictando_y_apaga_en_espera(ent: Entorno) -> None:
    s = ent.c.post("/sessions", json={"mode": "dictado"}).json()
    esperar(lambda: ent.c.get("/engine").json()["state"] == "dictando")
    assert ent.c.post("/engine/release").status_code == 409
    ent.c.post(f"/sessions/{s['id']}/stop")
    esperar(lambda: ent.sesion(s["id"])["status"] == "final")
    r = ent.c.post("/engine/release")
    assert r.status_code == 200 and r.json()["state"] == "apagado" and ent.fab.ultimo.cerrado


def test_liberar_en_pleno_dictado_cierra_la_sesion_y_apaga(ent: Entorno) -> None:
    """Lo que hace Ctrl+Alt+L: termina el dictado conservando su texto y luego libera la GPU."""
    s = ent.c.post("/sessions", json={"mode": "dictado"}).json()
    ent.hablar()
    esperar(lambda: ent.fab.ultimo.muestras == 3200)
    ent.c.portal.call(ent.sesiones.liberar, "prueba")  # type: ignore[union-attr]
    assert ent.sesion(s["id"])["status"] == "final"
    assert ent.sesion(s["id"])["text"] == "frase de 3200 muestras"
    assert ent.c.get("/engine").json()["state"] == "apagado" and ent.fab.ultimo.cerrado


def test_release_con_stop_session_termina_el_dictado_y_libera(ent: Entorno) -> None:
    s = ent.c.post("/sessions", json={"mode": "dictado"}).json()
    ent.hablar()
    esperar(lambda: ent.fab.ultimo.muestras == 3200)
    r = ent.c.post("/engine/release?stop_session=true")
    assert r.status_code == 200 and r.json()["state"] == "apagado"
    assert ent.sesion(s["id"])["text"] == "frase de 3200 muestras"


def test_liberar_sin_sesion_solo_apaga(ent: Entorno) -> None:
    ent.dictar()
    ent.c.portal.call(ent.sesiones.liberar, "prueba")  # type: ignore[union-attr]
    assert ent.c.get("/engine").json()["state"] == "apagado"


def test_toggle_inicia_y_detiene(ent: Entorno) -> None:
    s = ent.c.post("/sessions/toggle", json={"mode": "dictado"}).json()
    assert s["status"] == "grabando"
    ent.hablar()
    esperar(lambda: ent.fab.ultimo.muestras == 3200)
    assert ent.c.post("/sessions/toggle", json={"mode": "dictado"}).json()["id"] == s["id"]
    esperar(lambda: ent.sesion(s["id"])["status"] == "final")


def test_modo_pausado_rechaza_sesiones(ent: Entorno) -> None:
    assert ent.c.put("/engine/mode", json={"mode": "pausado"}).json()["mode"] == "pausado"
    assert ent.c.post("/sessions", json={"mode": "dictado"}).status_code == 409
    assert ent.c.put("/engine/mode", json={"mode": "turbo"}).status_code == 422


def test_exportar_en_markdown_y_texto(ent: Entorno) -> None:
    s = ent.dictar(modo="tarea")
    md = ent.c.get(f"/sessions/{s['id']}/export?format=md")
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.text.startswith("# Tarea ·") and "frase de 3200 muestras" in md.text
    assert ent.c.get(f"/sessions/{s['id']}/export?format=txt").text == "frase de 3200 muestras\n"
    assert ent.c.get(f"/sessions/{s['id']}/export?format=pdf").status_code == 422


def test_listar_y_errores_404(ent: Entorno) -> None:
    s = ent.dictar()
    assert [x["id"] for x in ent.c.get("/sessions").json()] == [s["id"]]
    assert ent.c.get("/sessions?status=grabando").json() == []
    assert ent.c.get("/sessions/noexiste").status_code == 404
    assert ent.c.post("/sessions/noexiste/stop").status_code == 404
    assert ent.c.post("/sessions", json={"mode": "dictado", "extra": 1}).status_code == 422


def test_shutdown_pide_el_cierre_despues_de_responder(ent: Entorno) -> None:
    r = ent.c.post("/shutdown")
    assert r.status_code == 202 and r.json() == {"status": "cerrando"}
    esperar(lambda: ent.cierres == ["cierre"])


def test_shutdown_suelta_los_websocket_abiertos(ent: Entorno) -> None:
    """Un cliente conectado no debe bloquear el apagado: recibe un cierre 1001."""
    from starlette.websockets import WebSocketDisconnect

    with ent.c.websocket_connect("/ws") as ws:
        ent.c.post("/shutdown")
        try:
            while True:
                ws.receive_text()
        except WebSocketDisconnect as e:
            assert e.code == 1001


def test_sin_espacio_en_gpu_forzada_responde_409(ent: Entorno) -> None:
    ent.c.put("/engine/mode", json={"mode": "gpu"})
    ent.sesiones._motor._gpu.libre = 100  # type: ignore[attr-defined]
    r = ent.c.post("/sessions", json={"mode": "dictado"})
    assert r.status_code == 409 and "5080" in r.json()["detail"]
