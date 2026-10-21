"""La lógica del panel traduce bien los eventos de voice-core, sin Qt."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from overlay.estado import COLORES_PUNTO, EstadoPanel

T0 = datetime(2026, 10, 21, 17, 0, 0, tzinfo=UTC)


def ev(tipo: str, **datos: object) -> dict:
    return {"type": tipo, "ts": T0.isoformat(), "seq": None, **datos}


def panel_conectado() -> EstadoPanel:
    e = EstadoPanel()
    e.fijar_conexion(True)
    return e


def test_un_dictado_completo_muestra_copia_y_programa_ocultar() -> None:
    e = panel_conectado()
    assert e.aplicar(ev("engine.state", state="arrancando", mode="auto", device="cuda", reason="")) == []
    assert e.aplicar(ev("session.started", session_id="s1", mode="dictado")) == ["mostrar", "cancelar_ocultar"]
    assert e.titulo(T0 + timedelta(seconds=3), 0) == "dictado · 0:03 · cargando el modelo…"
    e.aplicar(ev("engine.state", state="dictando", mode="auto", device="cuda", reason=""))
    e.aplicar(ev("segment.partial", session_id="s1", text="La figura"))
    assert "La figura…" in e.html() and "font-style:italic" in e.html()
    e.aplicar(ev("segment.final", session_id="s1", index=0, t0=0, t1=2, text="La figura 3 necesita leyenda."))
    e.aplicar(ev("segment.partial", session_id="s1", text="Además revisar"))
    assert e.parrafo == "La figura 3 necesita leyenda."
    assert e.titulo(T0 + timedelta(seconds=42), 0) == "dictado · 0:42 · 5080"
    assert e.color_punto == COLORES_PUNTO["dictando"]
    acciones = e.aplicar(ev("session.finalized", session_id="s1", text="La figura 3 necesita leyenda. Además revisar.",
                            duration_s=40, segments=2))
    assert acciones == ["copiar", "programar_ocultar"]
    assert e.ultimo_texto == "La figura 3 necesita leyenda. Además revisar."
    assert e.parcial == "" and "Copiado" in e.aviso and not e.grabando


def test_parciales_de_otra_sesion_o_tras_terminar_se_ignoran() -> None:
    e = panel_conectado()
    e.aplicar(ev("session.started", session_id="s1", mode="dictado"))
    e.aplicar(ev("segment.partial", session_id="otra", text="no"))
    assert e.parcial == ""
    e.aplicar(ev("session.finalized", session_id="s1", text="Hola.", duration_s=2, segments=1))
    e.aplicar(ev("segment.partial", session_id="s1", text="tarde"))
    assert e.parcial == ""


def test_sesion_sin_voz_no_copia() -> None:
    e = panel_conectado()
    e.aplicar(ev("session.started", session_id="s1", mode="tarea"))
    assert e.aplicar(ev("session.finalized", session_id="s1", text="", duration_s=3, segments=0)) == ["programar_ocultar"]
    assert "No se reconoció" in e.aviso and e.ultimo_texto == ""


def test_sin_copia_automatica_solo_avisa() -> None:
    e = EstadoPanel(copiar_al_terminar=False)
    e.fijar_conexion(True)
    e.aplicar(ev("session.started", session_id="s1", mode="dictado"))
    assert e.aplicar(ev("session.finalized", session_id="s1", text="Hola.", duration_s=2, segments=1)) == \
        ["programar_ocultar"]


def test_cuenta_atras_del_apagado_en_espera() -> None:
    e = panel_conectado()
    assert e.aplicar(ev("engine.state", state="en_espera", mode="auto", device="cuda", reason="")) == \
        ["consultar_motor"]
    e.fijar_apagado_en(105, ahora=1000.0)
    assert e.titulo(T0, 1000.0) == "listo en 5080 · se apaga en 1:45"
    assert e.titulo(T0, 1060.0) == "listo en 5080 · se apaga en 0:45"
    e.aplicar(ev("engine.state", state="apagado", mode="auto", device=None, reason=""))
    assert e.titulo(T0, 1060.0) == "motor apagado · GPU libre"


def test_cuentas_atras_con_reposo_en_ram() -> None:
    e = panel_conectado()
    e.aplicar(ev("engine.state", state="en_espera", mode="auto", device="cuda", reason=""))
    e.fijar_motor({"state": "en_espera", "mode": "auto", "device": "cuda", "rest_in_s": 90, "auto_off_in_s": 1890},
                  ahora=0.0)
    assert e.titulo(T0, 30.0) == "listo en 5080 · pasa a RAM en 1:00"
    assert e.aplicar(ev("engine.state", state="en_reposo", mode="auto", device="cuda", reason="")) == \
        ["consultar_motor"]
    e.fijar_motor({"state": "en_reposo", "mode": "auto", "device": "cuda", "rest_in_s": None, "auto_off_in_s": 1800},
                  ahora=100.0)
    assert e.titulo(T0, 160.0) == "en reposo (RAM) · arranca en <1 s · se apaga en 29:00"
    assert e.color_punto == COLORES_PUNTO["en_reposo"]


def test_errores_y_conexion() -> None:
    e = panel_conectado()
    assert e.aplicar(ev("engine.error", message="Micrófono: no disponible")) == ["mostrar"]
    assert e.aviso_error and "Micrófono" in e.aviso
    e.fijar_conexion(False)
    assert e.titulo(T0, 0) == "voice-core sin conexión" and e.color_punto == COLORES_PUNTO["apagado"]
    e.fijar_conexion(True)
    assert e.aviso == ""


def test_el_html_escapa_el_texto() -> None:
    e = panel_conectado()
    e.aplicar(ev("session.started", session_id="s1", mode="dictado"))
    e.aplicar(ev("segment.final", session_id="s1", index=0, t0=0, t1=1, text="usa <b> y & en el código"))
    assert "&lt;b&gt;" in e.html() and "&amp;" in e.html()


def test_cargar_ultimo_dictado_al_arrancar() -> None:
    e = panel_conectado()
    e.cargar_ultimo("Texto de ayer.")
    assert e.ultimo_texto == "Texto de ayer." and e.parrafo == "Texto de ayer."
