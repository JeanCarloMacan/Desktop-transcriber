"""Persistencia de tasks-service (`datos/tareas.db`): tareas, extracciones, eventos y estado del consumidor."""
from __future__ import annotations

from datetime import UTC, datetime

from voice_core.almacen import BaseSqlite, Fila

CAMPOS_EDITABLES = ("title", "category", "due", "notes", "status")


def ahora_iso() -> str:
    return datetime.now(UTC).isoformat()


class AlmacenTareas(BaseSqlite):
    PAQUETE = "tasks_service"

    # ------------------------------------------------------------ tareas

    def crear_tarea(self, titulo: str, categoria: str, estado: str, *, due: str | None = None,
                    notas: str | None = None, sesion: str | None = None, extraccion: int | None = None,
                    cita: str | None = None) -> Fila:
        ts = ahora_iso()
        cur = self._ejecutar(
            "INSERT INTO tasks (title, category, due, notes, status, session_id, extraction_id, quote, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (titulo, categoria, due, notas, estado, sesion, extraccion, cita, ts, ts))
        return self.tarea(int(cur.lastrowid))  # type: ignore[return-value]

    def tarea(self, id_: int) -> Fila | None:
        return self._uno("SELECT * FROM tasks WHERE id = ?", (id_,))

    def tareas(self, estado: str | None = None, categoria: str | None = None, limite: int = 500) -> list[Fila]:
        condiciones, params = [], []
        if estado:
            condiciones.append("status = ?")
            params.append(estado)
        if categoria:
            condiciones.append("category = ?")
            params.append(categoria)
        donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
        # pendientes: primero las que tienen fecha (la más próxima arriba); hechas: las últimas arriba
        orden = ("done_at DESC" if estado == "hecha" else
                 "due IS NULL, due, created_at DESC" if estado == "pendiente" else "created_at DESC, id")
        return self._todos(f"SELECT * FROM tasks {donde} ORDER BY {orden} LIMIT ?", (*params, limite))

    def actualizar_tarea(self, id_: int, cambios: dict[str, object]) -> Fila | None:
        cambios = {k: v for k, v in cambios.items() if k in CAMPOS_EDITABLES}
        if "status" in cambios:
            cambios["done_at"] = ahora_iso() if cambios["status"] == "hecha" else None
        cambios["updated_at"] = ahora_iso()
        columnas = ", ".join(f"{k} = ?" for k in cambios)
        self._ejecutar(f"UPDATE tasks SET {columnas} WHERE id = ?", (*cambios.values(), id_))
        return self.tarea(id_)

    def propuestas_de(self, sesion: str) -> list[Fila]:
        return self._todos("SELECT * FROM tasks WHERE session_id = ? AND status = 'propuesta' ORDER BY id", (sesion,))

    def recuento_por_categoria(self) -> dict[str, dict[str, int]]:
        filas = self._todos("SELECT category, status, COUNT(*) AS n FROM tasks "
                            "WHERE status IN ('pendiente', 'propuesta') GROUP BY category, status")
        resultado: dict[str, dict[str, int]] = {}
        for f in filas:
            resultado.setdefault(f["category"], {"pendiente": 0, "propuesta": 0})[f["status"]] = f["n"]
        return resultado

    # ------------------------------------------------------------ extracciones

    def crear_extraccion(self, sesion: str, origen: str) -> Fila:
        cur = self._ejecutar("INSERT INTO extractions (session_id, origin, status, created_at) "
                             "VALUES (?, ?, 'en_cola', ?)", (sesion, origen, ahora_iso()))
        return self.extraccion(int(cur.lastrowid))  # type: ignore[return-value]

    def extraccion(self, id_: int) -> Fila | None:
        return self._uno("SELECT * FROM extractions WHERE id = ?", (id_,))

    def extracciones(self, sesion: str | None = None, limite: int = 50) -> list[Fila]:
        if sesion:
            return self._todos("SELECT * FROM extractions WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                               (sesion, limite))
        return self._todos("SELECT * FROM extractions ORDER BY id DESC LIMIT ?", (limite,))

    def actualizar_extraccion(self, id_: int, **cambios: object) -> Fila:
        columnas = ", ".join(f"{k} = ?" for k in cambios)
        self._ejecutar(f"UPDATE extractions SET {columnas} WHERE id = ?", (*cambios.values(), id_))
        return self.extraccion(id_)  # type: ignore[return-value]

    def hay_extraccion_auto(self, sesion: str) -> bool:
        return self._uno("SELECT 1 FROM extractions WHERE session_id = ? AND origin = 'auto'", (sesion,)) is not None

    def extracciones_inacabadas(self) -> list[Fila]:
        return self._todos("SELECT * FROM extractions WHERE status IN ('en_cola', 'procesando') ORDER BY id")

    # ------------------------------------------------------------ estado del consumidor

    def leer_estado(self, clave: str) -> str | None:
        fila = self._uno("SELECT value FROM state WHERE key = ?", (clave,))
        return fila["value"] if fila else None

    def fijar_estado(self, clave: str, valor: str) -> None:
        self._ejecutar("INSERT INTO state (key, value) VALUES (?, ?) "
                       "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (clave, valor))
