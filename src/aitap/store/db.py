"""SQLite storage contract.

Contract version: 1 (2026-05-09)

The schema is stable across patch versions; breaking changes bump
`SCHEMA_VERSION` and require a migration registered in `MIGRATIONS`.
The schema_version table records which migrations have been applied
so existing .aitap/db.sqlite files can be upgraded in place.

Example consumer:

    from aitap.store.db import connect, init_db
    conn = connect(settings.db_path)
    init_db(conn)
    conn.execute("INSERT INTO prompts (...) VALUES (...)")
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aitap.scanner.models import Pipeline, PromptSite, ProviderEvidence

SCHEMA_VERSION = 1

# DDL is split per-table so future migrations can ALTER individual tables
# without re-emitting the whole schema.

DDL_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_PROMPTS = """
CREATE TABLE IF NOT EXISTS prompts (
    id              TEXT    PRIMARY KEY,           -- PromptSite.id
    name            TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    file            TEXT    NOT NULL,
    line_start      INTEGER NOT NULL,
    line_end        INTEGER NOT NULL,
    purpose         TEXT,                          -- nullable, filled by L2
    confidence      TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL,              -- full PromptSite.model_dump_json()
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_commit     TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name);
CREATE INDEX IF NOT EXISTS idx_prompts_file ON prompts(file);
"""

DDL_PIPELINES = """
CREATE TABLE IF NOT EXISTS pipelines (
    id              TEXT    PRIMARY KEY,           -- Pipeline.id
    name            TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL,              -- full Pipeline.model_dump_json()
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_commit     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipelines_name ON pipelines(name);
"""

DDL_PROMPT_VERSIONS = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_id       TEXT    NOT NULL,
    version         INTEGER NOT NULL,              -- monotonic per prompt_id, starts at 1
    template_json   TEXT    NOT NULL,              -- list[Message].model_dump_json()
    parameters_json TEXT    NOT NULL,
    note            TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by      TEXT    NOT NULL DEFAULT 'human',  -- 'human' | 'iteration'
    parent_version  INTEGER,                       -- the version this was derived from
    PRIMARY KEY (prompt_id, version),
    FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
);
"""

DDL_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT    PRIMARY KEY,         -- ULID or "{ts}-{prompt}-v{n}"
    target_kind       TEXT    NOT NULL,            -- 'prompt' | 'pipeline'
    target_id         TEXT    NOT NULL,
    target_version    INTEGER NOT NULL,
    dataset_id        TEXT,
    provider          TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    parameters_json   TEXT    NOT NULL,
    git_commit        TEXT,
    started_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    status            TEXT    NOT NULL DEFAULT 'running',  -- 'running' | 'done' | 'failed'
    cost_usd          REAL    NOT NULL DEFAULT 0.0,
    snapshot_dir      TEXT                                  -- relative to .aitap/runs/
);
CREATE INDEX IF NOT EXISTS idx_runs_target ON runs(target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
"""

DDL_SCORES = """
CREATE TABLE IF NOT EXISTS scores (
    run_id          TEXT    NOT NULL,
    case_index      INTEGER NOT NULL,              -- index into dataset
    judge_kind      TEXT    NOT NULL,              -- 'rule' | 'llm' | 'human'
    judge_name      TEXT    NOT NULL,              -- e.g., rule code or judge model name
    score           REAL,                          -- nullable for pass/fail
    pass_fail       TEXT,                          -- 'pass' | 'fail' | NULL
    rationale       TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, case_index, judge_kind, judge_name),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
"""

DDL_FEEDBACK = """
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    case_index      INTEGER NOT NULL,
    rating          INTEGER,                       -- -1 / 0 / +1
    ideal_answer    TEXT,                          -- user-provided gold output
    critique        TEXT,                          -- free-text feedback
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feedback_run ON feedback(run_id);
"""

DDL_PROVIDERS = """
CREATE TABLE IF NOT EXISTS providers_detected (
    project_root    TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    file            TEXT    NOT NULL,
    line_start      INTEGER NOT NULL,
    key_var_name    TEXT    NOT NULL,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_root, provider, file, key_var_name)
);
"""

ALL_DDL = (
    DDL_SCHEMA_VERSION,
    DDL_PROMPTS,
    DDL_PIPELINES,
    DDL_PROMPT_VERSIONS,
    DDL_RUNS,
    DDL_SCORES,
    DDL_FEEDBACK,
    DDL_PROVIDERS,
)


# Migrations are functions taking a connection and applying schema changes.
# Index N migrates from version N-1 to N. Index 0 is unused (no migration needed
# to reach the bootstrap version).
MIGRATIONS: list = []


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults for aitap."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cur.fetchone() is None:
        return 0
    cur = conn.execute("SELECT MAX(version) AS v FROM schema_version")
    row = cur.fetchone()
    return int(row["v"] or 0)


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if missing and apply pending migrations."""
    for ddl in ALL_DDL:
        conn.executescript(ddl)

    current = _current_version(conn)
    target = SCHEMA_VERSION
    for v in range(current + 1, target + 1):
        if v < len(MIGRATIONS):
            MIGRATIONS[v](conn)
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))


@contextmanager
def transaction(
    conn: sqlite3.Connection,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Context manager wrapping a BEGIN/COMMIT/ROLLBACK.

    Use when a logical operation spans multiple statements.

    Set ``immediate=True`` to issue ``BEGIN IMMEDIATE`` instead of the
    default deferred ``BEGIN``. SQLite's deferred BEGIN only takes the
    write lock on the first write, so two threads/processes can both read
    state and then race to write — the second loses with ``database is
    locked``. ``BEGIN IMMEDIATE`` acquires the reserved (write) lock up
    front, serialising the whole read-modify-write sequence. Pay this
    cost for monotonic-counter style updates (e.g. ``MAX(version) + 1``).
    """
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------------------
# DAO functions — built on top of the frozen DDL above
# ---------------------------------------------------------------------------


def upsert_prompt(
    conn: sqlite3.Connection,
    site: PromptSite,
    *,
    last_commit: str | None = None,
) -> None:
    """Insert or update a :class:`~aitap.scanner.models.PromptSite` row.

    Uses ``PromptSite.id`` as the primary key. On conflict, updates the
    payload + ``last_seen_at`` + ``last_commit`` while preserving
    ``first_seen_at`` so history isn't lost.
    """
    conn.execute(
        """
        INSERT INTO prompts
            (id, name, provider, file, line_start, line_end, purpose,
             confidence, payload_json, last_commit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name        = excluded.name,
            provider    = excluded.provider,
            file        = excluded.file,
            line_start  = excluded.line_start,
            line_end    = excluded.line_end,
            purpose     = excluded.purpose,
            confidence  = excluded.confidence,
            payload_json= excluded.payload_json,
            last_seen_at= datetime('now'),
            last_commit = excluded.last_commit
        """,
        (
            site.id,
            site.name,
            site.provider.value,
            site.location.file,
            site.location.line_start,
            site.location.line_end,
            site.purpose,
            site.confidence.value,
            site.model_dump_json(),
            last_commit,
        ),
    )


def upsert_pipeline(
    conn: sqlite3.Connection,
    pipeline: Pipeline,
    *,
    last_commit: str | None = None,
) -> None:
    """Insert or update a :class:`~aitap.scanner.models.Pipeline` row.

    Uses ``Pipeline.id`` as the primary key. Idempotent — safe to call on
    every scan even when the pipeline has not changed.
    """
    conn.execute(
        """
        INSERT INTO pipelines (id, name, payload_json, last_commit)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name        = excluded.name,
            payload_json= excluded.payload_json,
            last_seen_at= datetime('now'),
            last_commit = excluded.last_commit
        """,
        (
            pipeline.id,
            pipeline.name,
            pipeline.model_dump_json(),
            last_commit,
        ),
    )


def record_provider_evidence(
    conn: sqlite3.Connection,
    project_root: str,
    ev: ProviderEvidence,
) -> None:
    """Insert-or-ignore a :class:`~aitap.scanner.models.ProviderEvidence` row.

    PK is ``(project_root, provider, file, key_var_name)`` so repeated
    scans produce no duplicates; ``detected_at`` is preserved.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO providers_detected
            (project_root, provider, source, file, line_start, key_var_name)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            project_root,
            ev.provider.value,
            ev.source,
            ev.location.file,
            ev.location.line_start,
            ev.key_var_name,
        ),
    )


def read_prompts(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
) -> list[sqlite3.Row]:
    """Return prompt rows, optionally filtered by *name*.

    Returns raw :class:`sqlite3.Row` objects so callers decide whether to
    deserialise ``payload_json`` or just inspect columns.
    """
    if name is not None:
        cur = conn.execute("SELECT * FROM prompts WHERE name = ? ORDER BY first_seen_at", (name,))
    else:
        cur = conn.execute("SELECT * FROM prompts ORDER BY first_seen_at")
    return cur.fetchall()


def read_pipelines(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all pipeline rows ordered by first detection time."""
    cur = conn.execute("SELECT * FROM pipelines ORDER BY first_seen_at")
    return cur.fetchall()
