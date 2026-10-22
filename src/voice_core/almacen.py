"""Persistencia en SQLite: sesiones, segmentos y eventos. Las migraciones SQL son el contrato del esquema."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from importlib.resources import files
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

Fila = dict[str, Any]


class Almacen:
    def __init__(self, ruta: Path | str) -> None:
        en_memoria = str(ruta) == ":memory:"
        if not en_memoria:
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(ruta), check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        if not en_memoria:
            self._con.execute("PRAGMA journal_mode = WAL")
        self._con.execute("PRAGMA foreign_keys = ON")
        self.migrar()

    # ------------------------------------------------------------ esquema

    def version(self) -> int:
        return int(self._con.execute("PRAGMA user_version").fetchone()[0])

    def migrar(self) -> None:
        actual = self.version()
        carpeta = files("voice_core") / "migraciones"
        pendientes = sorted(
            (int(m.group(1)), f) for f in carpeta.iterdir()
            if (m := re.match(r"(\d+)_.*\.sql$", f.name)) and int(m.group(1)) > actual
        )
        for numero, archivo in pendientes:
            sql = archivo.read_text(encoding="utf-8")
            with self._lock:
                self._con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
            log.info("Migración %04d aplicada", numero)

    def cerrar(self) -> None:
        self._con.close()

    def _uno(self, sql: str, params: tuple = ()) -> Fila | None:
        with self._lock:
            fila = self._con.execute(sql, params).fetchone()
        return dict(fila) if fila else None

    def _todos(self, sql: str, params: tuple = ()) -> list[Fila]:
        with self._lock:
            return [dict(f) for f in self._con.execute(sql, params).fetchall()]

    def _ejecutar(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._con.execute(sql, params)

    # ------------------------------------------------------------ sesiones

    def crear_sesion(self, id_: str, modo: str, inicio: str) -> Fila:
        self._ejecutar("INSERT INTO sessions (id, mode, status, started_at) VALUES (?, ?, 'grabando', ?)",
                       (id_, modo, inicio))
        return self.sesion(id_)  # type: ignore[return-value]

    def fijar_motor(self, id_: str, dispositivo: str, modelo: str) -> None:
        self._ejecutar("UPDATE sessions SET device = ?, model = ? WHERE id = ?", (dispositivo, modelo, id_))

    def marcar_estado(self, id_: str, estado: str) -> None:
        self._ejecutar("UPDATE sessions SET status = ? WHERE id = ?", (estado, id_))

    def agregar_segmento(self, id_: str, indice: int, t0: float, t1: float, texto: str) -> None:
        self._ejecutar("INSERT INTO segments (session_id, idx, t0, t1, text) VALUES (?, ?, ?, ?, ?)",
                       (id_, indice, t0, max(t0, t1), texto))

    def finalizar_sesion(self, id_: str, fin: str, texto: str, audio: str | None) -> None:
        self._ejecutar("UPDATE sessions SET status = 'final', ended_at = ?, text = ?, audio_path = ? WHERE id = ?",
                       (fin, texto, audio, id_))

    _SESIONES = ("SELECT s.*, (SELECT COUNT(*) FROM attachments a WHERE a.session_id = s.id) AS attachments "
                 "FROM sessions s")

    def sesion(self, id_: str) -> Fila | None:
        return self._uno(f"{self._SESIONES} WHERE s.id = ?", (id_,))

    def segmentos(self, id_: str) -> list[Fila]:
        return self._todos("SELECT idx AS \"index\", t0, t1, text FROM segments WHERE session_id = ? ORDER BY idx",
                           (id_,))

    def listar_sesiones(self, limite: int, estado: str | None = None) -> list[Fila]:
        if estado:
            return self._todos(f"{self._SESIONES} WHERE s.status = ? ORDER BY s.started_at DESC LIMIT ?",
                               (estado, limite))
        return self._todos(f"{self._SESIONES} ORDER BY s.started_at DESC LIMIT ?", (limite,))

    # ------------------------------------------------------------ adjuntos y documento

    def agregar_adjunto(self, sesion: str, t: float, archivo: str, ancho: int | None, alto: int | None,
                        creado: str) -> Fila:
        cur = self._ejecutar("INSERT INTO attachments (session_id, kind, t, file, width, height, created_at) "
                             "VALUES (?, 'imagen', ?, ?, ?, ?, ?)", (sesion, max(0.0, t), archivo, ancho, alto, creado))
        return self.adjunto(sesion, int(cur.lastrowid))  # type: ignore[return-value]

    def adjunto(self, sesion: str, id_: int) -> Fila | None:
        return self._uno("SELECT * FROM attachments WHERE session_id = ? AND id = ?", (sesion, id_))

    def adjuntos(self, sesion: str) -> list[Fila]:
        return self._todos("SELECT * FROM attachments WHERE session_id = ? ORDER BY t, id", (sesion,))

    def guardar_documento(self, sesion: str, markdown: str | None, editado: str | None) -> None:
        self._ejecutar("UPDATE sessions SET document = ?, edited_at = ? WHERE id = ?", (markdown, editado, sesion))

    def cerrar_colgadas(self, fin: str) -> int:
        """Tras un cierre brusco: las sesiones que quedaron grabando o procesando pasan a final con lo que tengan."""
        colgadas = self._todos("SELECT id FROM sessions WHERE status != 'final'")
        for s in colgadas:
            texto = " ".join(f["text"] for f in self.segmentos(s["id"]))
            self.finalizar_sesion(s["id"], fin, texto, None)
        return len(colgadas)

    # ------------------------------------------------------------ eventos

    def guardar_evento(self, tipo: str, ts: str, sesion: str | None, payload: str) -> int:
        cur = self._ejecutar("INSERT INTO events (ts, type, session_id, payload) VALUES (?, ?, ?, ?)",
                             (ts, tipo, sesion, payload))
        return int(cur.lastrowid)

    def eventos_desde(self, despues: int, limite: int) -> list[Fila]:
        return self._todos("SELECT seq, payload FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (despues, limite))
