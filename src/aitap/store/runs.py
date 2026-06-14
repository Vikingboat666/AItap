"""DAO for run/score/feedback/prompt_version tables.

The DDL for these tables lives in :mod:`aitap.store.db` as a frozen contract.
This module supplies the read/insert helpers consumed by the Runs/Settings
HTTP routes and (later, in M4) by the iteration loop.

Why a separate module from ``store/db.py``?
- ``store/db.py`` is a *contract* file (schema + connection helper); changes
  there ripple to every worktree.
- ``store/runs.py`` is implementation-level: free to evolve as we learn more
  about which queries the playground actually needs.

All helpers take an open :class:`sqlite3.Connection` (autocommit) so callers
can compose them inside :func:`~aitap.store.db.transaction` if they need
atomicity across multiple statements.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from aitap.scanner.models import CallParameters


# Mirror the column-level CHECK semantics encoded in the DDL so a typo in
# Python land surfaces as a TypeError rather than a silent column write.
RunStatus = Literal["running", "done", "failed"]
TargetKind = Literal["prompt", "pipeline"]
CreatedBy = Literal["human", "iteration"]


def new_run_id(target_id: str, version: int) -> str:
    """Build a run id of the shape ``{epoch_ms}-{target}-v{n}-{shortuuid}``.

    The timestamp prefix makes the id sortable, the version suffix makes
    accidental collisions across reruns of the same target trivially
    diagnosable, and the uuid tail keeps two concurrent POSTs distinct.
    """
    ts = int(time.time() * 1000)
    short = uuid.uuid4().hex[:8]
    return f"{ts}-{target_id}-v{version}-{short}"


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    target_kind: TargetKind,
    target_id: str,
    target_version: int,
    parameters_json: str,
    dataset_id: str | None = None,
    profile_id: str | None = None,
    provider: str = "",
    model: str = "",
    status: RunStatus = "running",
    cost_usd: float = 0.0,
    git_commit: str | None = None,
    snapshot_dir: str | None = None,
) -> None:
    """Insert a fresh row into ``runs`` in the *running* state.

    ``parameters_json`` is the json-serialised
    :class:`~aitap.scanner.models.CallParameters` payload; we store it as
    text to avoid widening the schema every time we add a new parameter.

    ``profile_id`` records which multi-provider profile a run was
    dispatched against (schema v3 column). After contract v4 (A2-P3)
    this is the **single source of truth for which client served a
    run**. ``provider`` + ``model`` remain on the DDL with ``NOT NULL``
    constraints so legacy rows persisted under contract v3 / v2 still
    read back, but they have a tri-state semantics that callers need
    to handle:

    - Pre-v3 historical rows (and rows the iterate loop writes today):
      meaningful ``provider`` + ``model`` enum-shaped strings,
      ``profile_id = NULL``.
    - Contract v4 ``POST /api/runs`` rows: ``provider = ""``,
      ``model = ""``, ``profile_id`` set.

    **Readers that want the resolved provider/model must look it up
    via ``profile_id``** (e.g., ``aitap.config_io.load_profiles_from_yaml``
    → ``ProfileConfig.protocol`` / ``.model_id``). Treating
    ``row["provider"]`` as a non-empty enum will silently get ``""``
    for v4-dispatched rows. A follow-up worktree can drop the
    ``NOT NULL`` constraint via ``ALTER TABLE`` once the read-paths
    are audited.
    """
    conn.execute(
        """
        INSERT INTO runs
            (id, target_kind, target_id, target_version, dataset_id,
             provider, model, profile_id, parameters_json, git_commit,
             status, cost_usd, snapshot_dir)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            target_kind,
            target_id,
            target_version,
            dataset_id,
            provider,
            model,
            profile_id,
            parameters_json,
            git_commit,
            status,
            cost_usd,
            snapshot_dir,
        ),
    )


def update_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: RunStatus,
    cost_usd: float | None = None,
    finished: bool = False,
) -> None:
    """Move a run between status states and optionally stamp cost / finish time.

    Callers should pass ``finished=True`` when status is terminal (done/failed);
    we stamp ``finished_at`` to the current sqlite time so a separate clock
    isn't required.
    """
    sets: list[str] = ["status = ?"]
    params: list[object] = [status]
    if cost_usd is not None:
        sets.append("cost_usd = ?")
        params.append(cost_usd)
    if finished:
        sets.append("finished_at = datetime('now')")
    params.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", params)


def read_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    return cur.fetchone()


def list_runs(
    conn: sqlite3.Connection,
    *,
    target_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """List runs newest-first, optionally filtered by ``target_id``."""
    if target_id is not None:
        cur = conn.execute(
            "SELECT * FROM runs WHERE target_id = ? ORDER BY started_at DESC LIMIT ?",
            (target_id, limit),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# scores
# ---------------------------------------------------------------------------


def insert_score(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    case_index: int,
    judge_kind: Literal["rule", "llm", "human"],
    judge_name: str,
    score: float | None = None,
    pass_fail: Literal["pass", "fail"] | None = None,
    rationale: str | None = None,
) -> None:
    """Insert a score row.

    The PK ``(run_id, case_index, judge_kind, judge_name)`` rejects
    duplicates; callers that want to overwrite should delete-then-insert
    inside a transaction.
    """
    conn.execute(
        """
        INSERT INTO scores
            (run_id, case_index, judge_kind, judge_name, score, pass_fail, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, case_index, judge_kind, judge_name, score, pass_fail, rationale),
    )


def read_scores(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM scores WHERE run_id = ? ORDER BY case_index, judge_kind, judge_name",
        (run_id,),
    )
    return cur.fetchall()


def avg_score(conn: sqlite3.Connection, run_id: str) -> float | None:
    """Average numeric score across all judges, or ``None`` if no rows."""
    cur = conn.execute(
        "SELECT AVG(score) AS avg_score FROM scores WHERE run_id = ? AND score IS NOT NULL",
        (run_id,),
    )
    row = cur.fetchone()
    if row is None or row["avg_score"] is None:
        return None
    return float(row["avg_score"])


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------


def insert_feedback(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    case_index: int,
    rating: int | None = None,
    ideal_answer: str | None = None,
    critique: str | None = None,
) -> int:
    """Insert a feedback row and return its auto-incremented id.

    We re-read the row id via ``last_insert_rowid()`` rather than relying on
    ``cursor.lastrowid`` so the helper is usable on connections that wrap
    ``execute`` in a non-default way (e.g., during test instrumentation).
    """
    conn.execute(
        """
        INSERT INTO feedback (run_id, case_index, rating, ideal_answer, critique)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, case_index, rating, ideal_answer, critique),
    )
    cur = conn.execute("SELECT last_insert_rowid() AS id")
    row = cur.fetchone()
    return int(row["id"])


def read_feedback(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM feedback WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# prompt_versions  — minimal helpers needed by iterate_one_round
# ---------------------------------------------------------------------------


def latest_prompt_version(
    conn: sqlite3.Connection,
    prompt_id: str,
) -> int:
    """Return the highest version number for *prompt_id*, or 0 if none."""
    cur = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM prompt_versions WHERE prompt_id = ?",
        (prompt_id,),
    )
    row = cur.fetchone()
    return int(row["v"])


def insert_prompt_version(
    conn: sqlite3.Connection,
    *,
    prompt_id: str,
    version: int,
    template_json: str,
    parameters_json: str,
    note: str | None = None,
    created_by: CreatedBy = "human",
    parent_version: int | None = None,
) -> None:
    """Insert a new row into ``prompt_versions``.

    Versions are monotonic per prompt — callers must compute ``version``
    via :func:`latest_prompt_version` + 1 (inside a transaction if they
    care about race-free monotonicity).
    """
    conn.execute(
        """
        INSERT INTO prompt_versions
            (prompt_id, version, template_json, parameters_json,
             note, created_by, parent_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt_id,
            version,
            template_json,
            parameters_json,
            note,
            created_by,
            parent_version,
        ),
    )


def read_prompt_versions(
    conn: sqlite3.Connection,
    prompt_id: str,
) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM prompt_versions WHERE prompt_id = ? ORDER BY version",
        (prompt_id,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Helpers shared with the API layer
# ---------------------------------------------------------------------------


def serialize_parameters(parameters: CallParameters) -> str:
    """Serialise CallParameters to JSON text for the ``runs.parameters_json`` column.

    Centralised so any future field added to CallParameters automatically
    flows through (model_dump_json walks the pydantic model).
    """
    return parameters.model_dump_json()


def serialize_messages(messages: Sequence[object]) -> str:
    """Serialise a list of pydantic Message models to JSON for ``prompt_versions``.

    Accepts ``Sequence[object]`` rather than ``list[Message]`` so callers
    that already hold the data as dicts (e.g., during deserialisation
    round-trips) don't have to reinflate the model.
    """
    payload: list[object] = []
    for msg in messages:
        dump = getattr(msg, "model_dump", None)
        if callable(dump):
            payload.append(dump(mode="json"))
        else:
            payload.append(msg)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def parse_started_at(row: sqlite3.Row) -> datetime:
    """Parse the sqlite-formatted ``started_at`` column into a tz-naive datetime.

    SQLite ``datetime('now')`` writes ``YYYY-MM-DD HH:MM:SS`` (UTC, no offset).
    """
    raw = cast(str, row["started_at"])
    return datetime.fromisoformat(raw)


def parse_finished_at(row: sqlite3.Row) -> datetime | None:
    raw = row["finished_at"]
    if raw is None:
        return None
    return datetime.fromisoformat(cast(str, raw))
