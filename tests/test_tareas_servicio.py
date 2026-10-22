"""F4: tasks-service con un LLM y un voice-core falsos: extracciones, propuestas, tareas, consumidor y eventos."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from conftest import esperar
from tasks_service.almacen import AlmacenTareas
from tasks_service.api import ServiciosTareas, crear_app
from tasks_service.contratos import EVENTO_TAREAS, EVENTOS_EFIMEROS
from tasks_service.llm import ErrorLlm, Respuesta
from tasks_service.organizador import Organizador
from tasks_service.voz import SesionDesconocida, VoiceCoreNoResponde
from voice_core.ajustes import AjustesTareas
from voice_core.bus import Bus

CATEGORIAS = {"Trabajo": "proyectos y correos", "Personal": "casa y trámites"}


def tarea(title: str, category: str = "Trabajo", due: str | None = None) -> dict:
    return {"title": title, "category": category, "due": due, "notes": None, "quote": f"…{title.lower()}…"}


class LlmFalso:
    url = "http://lmstudio.falso:1234"
    modelo = "modelo-falso"
    ttl_s = 600

    def __init__(self) -> None:
        self.respuestas: list[object] = []  # str (contenido) o una excepción
        self.llamadas: list[list[dict]] = []
        self.vivo = True
        self.en_memoria = False  # el modelo cargado en LM Studio
        self.descargas = 0

    def responde(self) -> bool:
        return self.vivo

    def asegurar_servidor(self) -> bool:
        return self.vivo

    def cargado(self) -> bool | None:
        return self.en_memoria if self.vivo else None

    def descargar(self) -> int:
        self.descargas += 1
        n, self.en_memoria = int(self.en_memoria), False
        return n

    def completar(self, mensajes: list[dict], esquema: dict) -> Respuesta:
        self.llamadas.append(mensajes)
        self.en_memoria = True  # LM Studio lo carga a demanda
        r = self.respuestas.pop(0) if self.respuestas else json.dumps({"tasks": []})
        if isinstance(r, Exception):
            raise r
        return Respuesta(contenido=str(r), segundos=0.42)


class GpuFalsa:
    def __init__(self, ocupada: str | None = None) -> None:
        self.ocupada = ocupada  # motivo, o None si está libre
        self.consultas = 0

    def motivo(self) -> str | None:
        self.consultas += 1
        return self.ocupada


@dataclass
class VozFalsa:
    sesiones: dict[str, dict] = field(default_factory=dict)
    caida: bool = False

    def url_ws(self, despues: int) -> str:
        return f"ws://voz.falsa/ws?after={despues}"

    def agregar(self, id_: str, texto: str, modo: str = "tarea", estado: str = "final",
                inicio: str = "2026-10-21T22:00:00+00:00") -> None:
        self.sesiones[id_] = {"id": id_, "mode": modo, "status": estado, "started_at": inicio, "text": texto}

    def sesion(self, id_: str) -> dict:
        if self.caida:
            raise VoiceCoreNoResponde("voice-core no responde")
        if id_ not in self.sesiones:
            raise SesionDesconocida(f"voice-core no tiene la sesión {id_}")
        return self.sesiones[id_]

    def texto(self, id_: str) -> str:
        return self.sesion(id_)["text"]


@dataclass
class Entorno:
    c: TestClient
    llm: LlmFalso
    voz: VozFalsa
    almacen: AlmacenTareas
    org: Organizador

    def extraer(self, sesion: str) -> dict:
        r = self.c.post("/extractions", json={"session_id": sesion})
        assert r.status_code == 202, r.text
        ex = r.json()
        esperar(lambda: self.extraccion(ex["id"])["status"] in ("lista", "error"))
        return self.extraccion(ex["id"])

    def extraccion(self, id_: int) -> dict:
        return next(e for e in self.c.get("/extractions").json() if e["id"] == id_)

    def tareas(self, estado: str) -> list[dict]:
        return self.c.get(f"/tasks?status={estado}").json()


def organizador(llm: LlmFalso, voz: VozFalsa, almacen: AlmacenTareas | None = None) -> Organizador:
    almacen = almacen or AlmacenTareas(":memory:")
    return Organizador(AjustesTareas(categorias=CATEGORIAS), almacen, Bus(almacen, EVENTO_TAREAS, EVENTOS_EFIMEROS),
                       llm, voz)


@pytest.fixture
def ent() -> Iterator[Entorno]:
    llm, voz = LlmFalso(), VozFalsa()
    org = organizador(llm, voz)
    almacen = org._almacen  # noqa: SLF001
    sv = ServiciosTareas(almacen, org._bus, org, llm, escuchar_voz=False)  # noqa: SLF001
    with TestClient(crear_app(sv)) as c:
        yield Entorno(c, llm, voz, almacen, org)


def test_extraer_deja_propuestas_con_su_origen(ent: Entorno) -> None:
    ent.voz.agregar("s1", "Mandar el informe a Marta antes del viernes y comprar pilas.")
    ent.llm.respuestas.append(json.dumps({"tasks": [tarea("Enviar el informe a Marta", due="2026-10-23"),
                                                    tarea("Comprar pilas", "Personal")]}))
    ex = ent.extraer("s1")
    assert (ex["status"], ex["tasks"], ex["origin"], ex["model"], ex["duration_s"]) == \
        ("lista", 2, "manual", "modelo-falso", 0.42)
    propuestas = ent.tareas("propuesta")
    assert {(t["title"], t["category"], t["due"]) for t in propuestas} == {
        ("Enviar el informe a Marta", "Trabajo", "2026-10-23"), ("Comprar pilas", "Personal", None)}
    assert all(t["session_id"] == "s1" and t["extraction_id"] == ex["id"] and t["quote"] for t in propuestas)
    usuario = ent.llm.llamadas[0][1]["content"]
    assert "(2026-10-21)" in usuario and "- Personal: casa y trámites" in usuario and "- Otros:" in usuario


def test_repetir_la_extraccion_sustituye_solo_las_propuestas(ent: Entorno) -> None:
    ent.voz.agregar("s1", "texto")
    ent.llm.respuestas += [json.dumps({"tasks": [tarea("Primera"), tarea("Segunda")]}),
                           json.dumps({"tasks": [tarea("Tercera")]})]
    ent.extraer("s1")
    aprobada = next(t for t in ent.tareas("propuesta") if t["title"] == "Primera")
    assert ent.c.patch(f"/tasks/{aprobada['id']}", json={"status": "pendiente"}).json()["status"] == "pendiente"
    ent.extraer("s1")
    assert [t["title"] for t in ent.tareas("propuesta")] == ["Tercera"]
    assert [t["title"] for t in ent.tareas("pendiente")] == ["Primera"]
    assert [t["title"] for t in ent.tareas("descartada")] == ["Segunda"]


def test_errores_al_pedir_una_extraccion(ent: Entorno) -> None:
    ent.voz.agregar("grabando", "…", estado="grabando")
    assert ent.c.post("/extractions", json={"session_id": "grabando"}).status_code == 409
    assert ent.c.post("/extractions", json={"session_id": "no-existe"}).status_code == 404
    ent.voz.caida = True
    r = ent.c.post("/extractions", json={"session_id": "grabando"})
    assert r.status_code == 503 and "voice-core" in r.json()["detail"]


def test_sin_lm_studio_o_con_respuesta_rota_la_extraccion_queda_en_error(ent: Entorno) -> None:
    ent.voz.agregar("s1", "texto")
    ent.llm.vivo = False
    ex = ent.extraer("s1")
    assert ex["status"] == "error" and "LM Studio no responde" in ex["error"]
    ent.llm.vivo = True
    ent.llm.respuestas.append("no es JSON")
    assert ent.extraer("s1")["status"] == "error"
    ent.llm.respuestas.append(ErrorLlm("LM Studio respondió 404: model not found"))
    assert "model not found" in ent.extraer("s1")["error"]
    assert ent.tareas("propuesta") == []


def test_un_dictado_vacio_no_llama_al_modelo(ent: Entorno) -> None:
    ent.voz.agregar("s1", "   ")
    ex = ent.extraer("s1")
    assert (ex["status"], ex["tasks"]) == ("lista", 0) and ent.llm.llamadas == []


def test_aprobar_editar_hecha_y_reabrir(ent: Entorno) -> None:
    ent.voz.agregar("s1", "texto")
    ent.llm.respuestas.append(json.dumps({"tasks": [tarea("Llamar al banco", "Personal", "2026-10-22")]}))
    ent.extraer("s1")
    t = ent.tareas("propuesta")[0]
    r = ent.c.patch(f"/tasks/{t['id']}", json={"status": "pendiente", "category": "trabajo", "due": None,
                                                "notes": "por la transferencia"}).json()
    assert (r["status"], r["category"], r["due"], r["notes"]) == ("pendiente", "Trabajo", None, "por la transferencia")
    hecha = ent.c.patch(f"/tasks/{t['id']}", json={"status": "hecha"}).json()
    assert hecha["done_at"] is not None
    reabierta = ent.c.patch(f"/tasks/{t['id']}", json={"status": "pendiente"}).json()
    assert reabierta["done_at"] is None and reabierta["title"] == "Llamar al banco"
    assert ent.c.patch(f"/tasks/{t['id']}", json={"status": "propuesta"}).status_code == 422
    assert ent.c.patch(f"/tasks/{t['id']}", json={"category": "Inventada"}).status_code == 422
    assert ent.c.patch("/tasks/999", json={"status": "hecha"}).status_code == 404


def test_tareas_a_mano_y_categorias_con_recuento(ent: Entorno) -> None:
    r = ent.c.post("/tasks", json={"title": "  Renovar la licencia ", "category": "personal", "due": "2026-10-31"})
    assert r.status_code == 201
    assert (r.json()["title"], r.json()["category"], r.json()["status"]) == ("Renovar la licencia", "Personal",
                                                                             "pendiente")
    assert ent.c.post("/tasks", json={"title": "x", "category": "Inventada"}).status_code == 422
    assert ent.c.post("/tasks", json={"title": "", "category": "Personal"}).status_code == 422
    ent.almacen.crear_tarea("De una categoría quitada", "Docencia", "pendiente")
    cats = {c["name"]: c for c in ent.c.get("/categories").json()}
    assert list(cats) == ["Trabajo", "Personal", "Otros", "Docencia"]
    assert (cats["Personal"]["pending"], cats["Personal"]["configured"]) == (1, True)
    assert (cats["Docencia"]["pending"], cats["Docencia"]["configured"]) == (1, False)


def test_pendientes_ordenadas_por_fecha_y_eventos_por_websocket(ent: Entorno) -> None:
    for titulo, due in [("Sin fecha", None), ("Luego", "2026-11-03"), ("Antes", "2026-10-22")]:
        ent.c.post("/tasks", json={"title": titulo, "category": "Trabajo", "due": due})
    assert [t["title"] for t in ent.tareas("pendiente")] == ["Antes", "Luego", "Sin fecha"]
    with ent.c.websocket_connect("/ws?after=0") as ws:
        tipos = [json.loads(ws.receive_text())["type"] for _ in range(3)]
    assert tipos == ["task.created"] * 3
    assert ent.c.get("/llm").json() == {"url": "http://lmstudio.falso:1234", "model": "modelo-falso",
                                        "reachable": True, "loaded": False, "ttl_s": 600}
    assert ent.c.get("/health").json()["voice_core"] is False


# ---------------------------------------------------------------- consumidor de voice-core (sin red)

def ev(tipo: str, seq: int, ts: str = "2099-01-01T00:00:00+00:00", **datos: object) -> dict:
    return {"type": tipo, "seq": seq, "ts": ts, **datos}


def test_los_dictados_de_tarea_se_encolan_solos_una_vez() -> None:
    llm, voz = LlmFalso(), VozFalsa()
    org = organizador(llm, voz)

    async def recorrido() -> None:
        await org.al_evento_voz(ev("session.started", 1, session_id="t1", mode="tarea"))
        await org.al_evento_voz(ev("session.finalized", 2, session_id="t1", text="Comprar pan.", duration_s=2,
                                   segments=1))
        await org.al_evento_voz(ev("session.started", 3, session_id="d1", mode="dictado"))
        await org.al_evento_voz(ev("session.finalized", 4, session_id="d1", text="Un comentario.", duration_s=2,
                                   segments=1))
        await org.al_evento_voz(ev("session.finalized", 2, session_id="t1", text="Comprar pan.", duration_s=2,
                                   segments=1))  # repetido (reconexión): no se vuelve a encolar
        await org.al_evento_voz(ev("segment.partial", None, session_id="x", text="…"))

    asyncio.run(recorrido())
    extracciones = org._almacen.extracciones()  # noqa: SLF001
    assert [(e["session_id"], e["origin"], e["status"]) for e in extracciones] == [("t1", "auto", "en_cola")]
    assert org._almacen.leer_estado("voice_seq") == "4"  # noqa: SLF001


def test_dictados_anteriores_a_la_instalacion_o_sin_modo_conocido() -> None:
    voz = VozFalsa()
    voz.agregar("viejo", "texto")
    voz.agregar("antes_de_conectar", "texto")
    org = organizador(LlmFalso(), voz)

    async def recorrido() -> None:
        await org.al_evento_voz(ev("session.finalized", 7, ts="2020-01-01T00:00:00+00:00", session_id="viejo",
                                   text="texto", duration_s=1, segments=1))
        await org.al_evento_voz(ev("session.finalized", 8, session_id="antes_de_conectar", text="texto",
                                   duration_s=1, segments=1))  # sin session.started: pregunta el modo por REST
        await org.al_evento_voz(ev("session.finalized", 9, session_id="vacio", text="  ", duration_s=1, segments=0))

    asyncio.run(recorrido())
    assert [e["session_id"] for e in org._almacen.extracciones()] == ["antes_de_conectar"]  # noqa: SLF001


def test_al_arrancar_retoma_las_extracciones_a_medias() -> None:
    llm, voz = LlmFalso(), VozFalsa()
    voz.agregar("s1", "texto")
    llm.respuestas.append(json.dumps({"tasks": [tarea("Retomada")]}))
    almacen = AlmacenTareas(":memory:")
    fila = almacen.crear_extraccion("s1", "auto")
    almacen.actualizar_extraccion(fila["id"], status="procesando")
    org = organizador(llm, voz, almacen)

    async def recorrido() -> None:
        org.arrancar(escuchar_voz=False)
        for _ in range(100):
            if almacen.extraccion(fila["id"])["status"] == "lista":
                break
            await asyncio.sleep(0.01)
        await org.parar()

    asyncio.run(recorrido())
    assert almacen.extraccion(fila["id"])["status"] == "lista"
    assert [t["title"] for t in almacen.tareas("propuesta")] == ["Retomada"]


async def hasta(condicion: object, veces: int = 300) -> None:
    for _ in range(veces):
        if condicion():  # type: ignore[operator]
            return
        await asyncio.sleep(0.01)
    raise AssertionError("la condición no se cumplió a tiempo")


def test_con_la_gpu_ocupada_espera_sin_llamar_al_modelo_y_sigue_sola() -> None:
    llm, voz, gpu = LlmFalso(), VozFalsa(), GpuFalsa("la RTX 5090 está ocupada (3,2 GB en uso)")
    voz.agregar("s1", "Mandar el informe.")
    llm.respuestas.append(json.dumps({"tasks": [tarea("Enviar el informe")]}))
    almacen = AlmacenTareas(":memory:")
    org = Organizador(AjustesTareas(categorias=CATEGORIAS, reintento_gpu_s=0.01), almacen,
                      Bus(almacen, EVENTO_TAREAS, EVENTOS_EFIMEROS), llm, voz, gpu=gpu)

    async def recorrido() -> None:
        org.arrancar(escuchar_voz=False)
        ex = org._encolar("s1", "auto")  # noqa: SLF001
        await hasta(lambda: gpu.consultas >= 3)
        espera = org.extraccion_out(almacen.extraccion(ex.id))  # type: ignore[arg-type]
        assert (espera.status, espera.waiting) == ("en_cola", "la RTX 5090 está ocupada (3,2 GB en uso)")
        assert not llm.llamadas
        gpu.ocupada = None  # el otro programa terminó
        await hasta(lambda: almacen.extraccion(ex.id)["status"] == "lista")
        await hasta(lambda: llm.descargas == 1)
        await org.parar()

    asyncio.run(recorrido())
    fila = almacen.extraccion(1)
    assert fila is not None and fila["waiting"] is None and fila["tasks"] == 1
    assert not llm.en_memoria  # descargado al vaciarse la cola


def test_con_el_modelo_ya_cargado_no_mira_la_gpu_ni_lo_descarga(ent: Entorno) -> None:
    ent.voz.agregar("s1", "texto")
    ent.extraer("s1")  # lo carga el organizador: se descarga al terminar
    esperar(lambda: ent.llm.descargas == 1)
    assert not ent.llm.en_memoria
    ent.org._gpu = gpu = GpuFalsa("ocupada")  # noqa: SLF001
    ent.llm.en_memoria = True  # lo cargaste tú en LM Studio
    assert ent.extraer("s1")["status"] == "lista"
    time.sleep(0.1)
    assert gpu.consultas == 0 and ent.llm.descargas == 1 and ent.llm.en_memoria


def test_sin_descargar_al_terminar_se_queda_el_ttl() -> None:
    llm, voz = LlmFalso(), VozFalsa()
    voz.agregar("s1", "texto")
    almacen = AlmacenTareas(":memory:")
    org = Organizador(AjustesTareas(categorias=CATEGORIAS, descargar_al_terminar=False), almacen,
                      Bus(almacen, EVENTO_TAREAS, EVENTOS_EFIMEROS), llm, voz)

    async def recorrido() -> None:
        org.arrancar(escuchar_voz=False)
        ex = org._encolar("s1", "manual")  # noqa: SLF001
        await hasta(lambda: almacen.extraccion(ex.id)["status"] == "lista")
        await asyncio.sleep(0.05)
        await org.parar()

    asyncio.run(recorrido())
    assert llm.descargas == 0 and llm.en_memoria
