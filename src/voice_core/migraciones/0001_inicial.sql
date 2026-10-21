-- 0001 · esquema inicial de voice-core (F1)
-- Fechas en ISO-8601 UTC (TEXT). Los eventos efímeros (segment.partial) no se guardan.

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    mode        TEXT NOT NULL CHECK (mode IN ('dictado', 'tarea')),
    status      TEXT NOT NULL CHECK (status IN ('grabando', 'procesando', 'final')),
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    text        TEXT NOT NULL DEFAULT '',
    device      TEXT CHECK (device IS NULL OR device IN ('cuda', 'cpu')),
    model       TEXT,
    audio_path  TEXT
);
CREATE INDEX IF NOT EXISTS sessions_started_at_idx ON sessions (started_at);
CREATE INDEX IF NOT EXISTS sessions_status_idx ON sessions (status);

CREATE TABLE IF NOT EXISTS segments (
    session_id  TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL CHECK (idx >= 0),
    t0          REAL NOT NULL CHECK (t0 >= 0),
    t1          REAL NOT NULL CHECK (t1 >= t0),
    text        TEXT NOT NULL,
    PRIMARY KEY (session_id, idx)
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    type        TEXT NOT NULL,
    session_id  TEXT REFERENCES sessions (id) ON DELETE SET NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session_id_idx ON events (session_id);
