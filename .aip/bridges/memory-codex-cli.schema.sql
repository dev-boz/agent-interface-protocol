-- Stable, versioned schema for the Codex CLI memory adapter's local SQLite
-- cache (spec §"Memory Adapters"). This DB is a DERIVED cache only — the
-- canonical store is gitmem (~/.gitmem/memory/) or local files. The adapter
-- reads only local files and this local DB; it writes session summaries and
-- extracted facts to gitmem on session_end.
--
-- schema_version: 1  (must match memory-codex-cli.yaml schema_version)

PRAGMA user_version = 1;

-- One row per agent session observed by the adapter.
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    agent        TEXT NOT NULL,
    started_at   TEXT NOT NULL,           -- ISO-8601 UTC
    ended_at     TEXT,                    -- ISO-8601 UTC, NULL while live
    summary      TEXT,                    -- extracted on session_end
    promoted     INTEGER NOT NULL DEFAULT 0  -- 1 once appended to gitmem
);

-- Extracted facts/observations cached locally before promotion to gitmem.
CREATE TABLE IF NOT EXISTS facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    kind         TEXT NOT NULL,           -- e.g. 'fact', 'decision', 'todo'
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL,           -- ISO-8601 UTC
    promoted     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent);
