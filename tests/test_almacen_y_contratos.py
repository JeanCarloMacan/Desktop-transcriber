"""Esquema SQLite, contratos de eventos y deriva de contracts/, y parseo de hotkeys."""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_core.almacen import Almacen
from voice_core.contratos import EVENTO, SessionStartIn
from voice_core.hotkeys import parsear

RAIZ = Path(__file__).resolve().parents[1]


def test_migracion_inicial_crea_el_esquema() -> None:
    a = Almacen(":memory:")
    assert a.version() == 1
    a.migrar()  # idempotente
    assert a.version() == 1


def test_sesiones_segmentos_y_colgadas() -> None:
    a = Almacen(":memory:")
    a.crear_sesion("s1", "dictado", "2026-10-21T10:00:00+00:00")
    a.agregar_segmento("s1", 1, 2.0, 3.0, "mundo")
    a.agregar_segmento("s1", 0, 0.0, 1.5, "hola")
    assert [s["text"] for s in a.segmentos("s1")] == ["hola", "mundo"]
    assert a.cerrar_colgadas("2026-10-21T10:05:00+00:00") == 1
    s = a.sesion("s1")
    assert s["status"] == "final" and s["text"] == "hola mundo"


def test_el_esquema_rechaza_valores_fuera_del_contrato() -> None:
    import sqlite3

    a = Almacen(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        a.crear_sesion("s1", "otro", "2026-10-21T10:00:00+00:00")


def test_eventos_se_validan_por_tipo() -> None:
    ts = datetime.now(UTC).isoformat()
    ev = EVENTO.validate_python({"type": "segment.final", "seq": 3, "ts": ts, "session_id": "s", "index": 0,
                                 "t0": 0, "t1": 1.5, "text": "hola"})
    assert ev.type == "segment.final" and ev.seq == 3
    with pytest.raises(ValidationError):
        EVENTO.validate_python({"type": "segment.final", "seq": 3, "ts": ts, "session_id": "s", "text": "x"})
    with pytest.raises(ValidationError):
        EVENTO.validate_python({"type": "otro", "seq": 1, "ts": ts})


def test_entradas_rechazan_campos_desconocidos() -> None:
    with pytest.raises(ValidationError):
        SessionStartIn.model_validate({"mode": "dictado", "x": 1})
    with pytest.raises(ValidationError):
        SessionStartIn.model_validate({"mode": "reunion"})


def test_contracts_coincide_con_los_modelos() -> None:
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "exportar_contratos.py"), "--comprobar"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(("combo", "esperado"), [
    ("ctrl+alt+space", (0x3, 0x20)),
    ("Ctrl+Alt+T", (0x3, 0x54)),
    ("win+shift+f9", (0xC, 0x78)),
])
def test_parsear_hotkeys(combo: str, esperado: tuple[int, int]) -> None:
    assert parsear(combo) == esperado


@pytest.mark.parametrize("combo", ["", "ctrl+", "hiper+a", "ctrl+alt+espaciador"])
def test_parsear_hotkeys_invalidas(combo: str) -> None:
    with pytest.raises(ValueError):
        parsear(combo)
