"""F3: capturas adjuntas, documento editable y exportación para chat y agente, por la API."""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from test_api import Entorno, ent  # noqa: F401  (fixture)

from conftest import esperar


def png(ancho: int = 4, alto: int = 3) -> bytes:
    def bloque(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    crudo = b"".join(b"\x00" + b"\xff\x00\x00" * ancho for _ in range(alto))
    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(crudo)) + bloque(b"IEND", b""))


def subir(ent: Entorno, sid: str, datos: bytes, t: float | None = None):  # type: ignore[no-untyped-def]
    ruta = f"/sessions/{sid}/attachments" + (f"?t={t}" if t is not None else "")
    return ent.c.post(ruta, content=datos, headers={"Content-Type": "image/png"})


def test_captura_durante_el_dictado_queda_tras_la_frase(ent: Entorno) -> None:
    s = ent.c.post("/sessions", json={"mode": "dictado"}).json()
    ent.hablar()
    esperar(lambda: ent.fab.ultimo.muestras == 3200)
    r = subir(ent, s["id"], png(800, 450), t=0.1)
    assert r.status_code == 201
    adj = r.json()
    assert (adj["file"], adj["width"], adj["height"], adj["t"]) == ("capturas/001.png", 800, 450, 0.1)
    assert Path(adj["path"]).is_file()
    ent.c.post(f"/sessions/{s['id']}/stop")
    esperar(lambda: ent.sesion(s["id"])["status"] == "final")
    detalle = ent.sesion(s["id"])
    assert detalle["attachments"] == 1 and detalle["attachment_items"][0]["id"] == adj["id"]
    doc = ent.c.get(f"/sessions/{s['id']}/document").json()
    assert doc["edited"] is False and doc["images"] == 1
    assert doc["markdown"].split("\n\n")[1:] == ["frase de 3200 muestras", "![Imagen 1](capturas/001.png)\n"]
    assert (Path(doc["folder"]) / "documento.md").read_text(encoding="utf-8") == doc["markdown"]
    tipos = [e["type"] for e in ent.c.get("/events?after=0").json()]
    assert "attachment.added" in tipos


def test_sin_t_tras_terminar_la_captura_va_al_final(ent: Entorno) -> None:
    s = ent.dictar()
    adj = subir(ent, s["id"], png()).json()
    assert adj["t"] >= 0
    md = ent.c.get(f"/sessions/{s['id']}/document").json()["markdown"]
    assert md.rstrip().endswith("![Imagen 1](capturas/001.png)")


def test_rechaza_lo_que_no_es_png_y_lo_demasiado_grande(ent: Entorno) -> None:
    s = ent.dictar()
    assert subir(ent, s["id"], b"GIF89a....").status_code == 415
    assert subir(ent, s["id"], png()[:20] + b"x" * (26 * 2**20)).status_code == 413
    assert subir(ent, "noexiste", png()).status_code == 404
    assert ent.sesion(s["id"])["attachments"] == 0


def test_descargar_la_captura(ent: Entorno) -> None:
    s = ent.dictar()
    datos = png(10, 10)
    adj = subir(ent, s["id"], datos).json()
    r = ent.c.get(adj["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == datos
    assert ent.c.get(f"/sessions/{s['id']}/attachments/999").status_code == 404


def test_editar_guardar_y_volver_al_original(ent: Entorno) -> None:
    s = ent.dictar()
    subir(ent, s["id"], png())
    original = ent.c.get(f"/sessions/{s['id']}/document").json()["markdown"]
    partido = ("# Mi revisión\n\nTexto corregido a mano que el editor partió en\nvarias líneas.\n\n"
               "![captura](capturas/001.png)\n")
    r = ent.c.put(f"/sessions/{s['id']}/document", json={"markdown": partido})
    assert r.status_code == 200
    doc = r.json()
    assert doc["edited"] is True and doc["edited_at"]
    assert doc["markdown"] == ("# Mi revisión\n\nTexto corregido a mano que el editor partió en varias líneas.\n\n"
                               "![Imagen 1](capturas/001.png)\n")
    assert ent.sesion(s["id"])["edited_at"] is not None
    assert ent.c.get(f"/sessions/{s['id']}/export?format=chat").text == (
        "Texto corregido a mano que el editor partió en varias líneas.\n\n[Imagen 1]\n")
    agente = ent.c.get(f"/sessions/{s['id']}/export?format=md").text
    ruta = agente.split("](<")[1].split(">)")[0]
    assert Path(ruta).is_file()
    assert ent.c.get(f"/sessions/{s['id']}/export?format=txt").text == (
        "Texto corregido a mano que el editor partió en varias líneas.\n")
    vuelta = ent.c.delete(f"/sessions/{s['id']}/document").json()
    assert vuelta["edited"] is False and vuelta["markdown"] == original
    editados = [e for e in ent.c.get("/events?after=0").json() if e["type"] == "session.edited"]
    assert [e["edited_at"] is None for e in editados] == [False, True]


def test_captura_despues_de_editar_se_anade_al_final(ent: Entorno) -> None:
    s = ent.dictar()
    ent.c.put(f"/sessions/{s['id']}/document", json={"markdown": "# Editado\n\nTexto mío.\n"})
    subir(ent, s["id"], png())
    md = ent.c.get(f"/sessions/{s['id']}/document").json()["markdown"]
    assert md == "# Editado\n\nTexto mío.\n\n![Imagen 1](capturas/001.png)\n"


def test_no_se_edita_mientras_graba(ent: Entorno) -> None:
    s = ent.c.post("/sessions", json={"mode": "dictado"}).json()
    r = ent.c.put(f"/sessions/{s['id']}/document", json={"markdown": "x"})
    assert r.status_code == 409 and "grabando" in r.json()["detail"]


def test_el_websocket_avisa_de_capturas_y_ediciones(ent: Entorno) -> None:
    s = ent.dictar()
    with ent.c.websocket_connect("/ws") as ws:
        subir(ent, s["id"], png())
        ev = json.loads(ws.receive_text())
        assert ev["type"] == "attachment.added" and ev["attachment"]["file"] == "capturas/001.png"
        ent.c.put(f"/sessions/{s['id']}/document", json={"markdown": "# T\n\nhola\n"})
        ev = json.loads(ws.receive_text())
        assert ev["type"] == "session.edited" and ev["session_id"] == s["id"]
