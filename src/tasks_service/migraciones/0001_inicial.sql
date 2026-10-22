-- tasks-service: tareas, extracciones (una por llamada al modelo), eventos y estado del consumidor.

CREATE TABLE extractions (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('auto', 'manual')),
    status      TEXT NOT NULL CHECK (status IN ('en_cola', 'procesando', 'lista', 'error')),
    model       TEXT,
    tasks       INTEGER NOT NULL DEFAULT 0,
    duration_s  REAL,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX extractions_sesion ON extractions (session_id);

CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL CHECK (length(title) > 0),
    category      TEXT NOT NULL,
    due           TEXT CHECK (due IS NULL OR due GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    notes         TEXT,
    status        TEXT NOT NULL CHECK (status IN ('propuesta', 'pendiente', 'hecha', 'descartada')),
    session_id    TEXT,
    extraction_id INTEGER REFERENCES extractions (id),
    quote         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    done_at       TEXT
);
CREATE INDEX tasks_estado ON tasks (status, category);
CREATE INDEX tasks_sesion ON tasks (session_id);

CREATE TABLE events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,
    session_id TEXT,
    payload    TEXT NOT NULL
);

-- clave/valor: «voice_seq» (último evento de voice-core procesado) e «instalado» (desde cuándo hay organizador)
CREATE TABLE state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
