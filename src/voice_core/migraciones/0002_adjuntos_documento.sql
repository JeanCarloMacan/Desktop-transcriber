-- 0002 · capturas adjuntas y documento editable (F3)

CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('imagen')),
    t           REAL NOT NULL CHECK (t >= 0),           -- segundos desde el inicio de la sesión
    file        TEXT NOT NULL,                          -- relativo a datos/sesiones/<id>/, p. ej. capturas/001.png
    width       INTEGER CHECK (width IS NULL OR width > 0),
    height      INTEGER CHECK (height IS NULL OR height > 0),
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attachments_session_id_idx ON attachments (session_id);

-- Markdown editado a mano; NULL = el documento se genera a partir de segmentos y adjuntos.
ALTER TABLE sessions ADD COLUMN document TEXT;
ALTER TABLE sessions ADD COLUMN edited_at TEXT;
