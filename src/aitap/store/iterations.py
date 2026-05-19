"""DAO for the ``iterations`` event log (Wave 4 — self-iteration loop).

Why a dedicated module:

- ``store/db.py`` is the frozen schema contract — it owns the DDL only.
- ``store/runs.py`` already covers the *runs / scores / feedback / prompt_versions*
  family. ``iterations`` is the M4 sibling table with its own write semantics
  (mutable ``downstream_status`` json after the row is committed), so we keep
  it in its own module to avoid bloating ``runs.py``.

All helpers take an open :class:`sqlite3.Connection` (autocommit) and never
open / close connections themselves. Callers compose them inside
:func:`~aitap.store.db.transaction` when they need atomicity.

The :class:`Iteration` model mirrors the row shape for typed downstream
consumers (server routes, the iterate loop, UI history queries). It is
intentionally identical to the DB columns — adding derived fields belongs
in higher layers, not here.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Literal, cast

from pydantic import BaseModel, Field

__all__ = [
    "ConvergedReason",
    "DownstreamStatus",
    "Iteration",
    "ReviseMode",
    "insert_iteration",
    "latest_iteration_for",
    "new_session_id",
    "parse_downstream_status",
    "parse_per_dim_scores",
    "read_iterations_for",
    "read_session",
    "serialize_downstream_status",
    "serialize_per_dim_scores",
    "update_downstream_status",
]


# Mirror the column-level enum semantics so a typo surfaces as a TypeError in
# Python land rather than a silent string in the DB.
ReviseMode = Literal["auto", "guided", "manual"]
ConvergedReason = Literal["max_rounds", "delta", "stagnation"]
# Per Decision 4 the impact analyzer flips 'unverified' to one of the others
# as the user re-runs downstream nodes.
DownstreamStatus = Literal["unverified", "verified", "regressed", "improved"]


# ---------------------------------------------------------------------------
# Session id (ULID)
# ---------------------------------------------------------------------------


# Crockford base32 alphabet — no I, L, O, U so eyeballed session ids in the
# UI don't get misread. Matches the ULID spec (https://github.com/ulid/spec).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_session_id() -> str:
    """Return a fresh 26-character ULID.

    The first 10 chars encode a millisecond timestamp (sortable lexically),
    the trailing 16 chars are CSPRNG-backed entropy. Pure stdlib —
    deliberately *not* pulling in the ``python-ulid`` package so this module
    doesn't widen the dependency surface for one helper.
    """
    ts_ms = int(time.time() * 1000)
    # 10 base32 chars carry 50 bits — plenty for a 48-bit ms timestamp.
    timestamp_part = _encode_base32(ts_ms, length=10)
    # 16 base32 chars carry 80 bits of entropy.
    randomness = secrets.randbits(80)
    random_part = _encode_base32(randomness, length=16)
    return timestamp_part + random_part


def _encode_base32(value: int, *, length: int) -> str:
    """Encode an integer as a fixed-length Crockford-base32 string.

    We zero-pad on the left so two session ids generated in the same
    millisecond still sort lexically by the randomness tail.
    """
    chars: list[str] = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


# ---------------------------------------------------------------------------
# Iteration model
# ---------------------------------------------------------------------------


class Iteration(BaseModel):
    """Typed view of one ``iterations`` row.

    Mirrors the column set 1:1. ``per_dim_scores`` and ``downstream_status``
    are parsed into Python dicts so callers don't repeat the JSON juggling
    everywhere; the raw DB column stays text-JSON so the schema is portable.
    """

    id: str
    prompt_id: str
    round: int
    session_id: str
    is_baseline: bool
    parent_version: int | None = None
    new_version: int | None = None
    revise_mode: ReviseMode | None = None
    revise_instruction: str | None = None
    critique_text: str | None = None
    weighted_score: float
    per_dim_scores: dict[str, float] = Field(default_factory=dict)
    downstream_status: dict[str, str] | None = None
    converged_reason: ConvergedReason | None = None
    started_at: datetime
    finished_at: datetime | None = None


# ---------------------------------------------------------------------------
# JSON serialise / parse helpers
# ---------------------------------------------------------------------------


def serialize_per_dim_scores(scores: dict[str, float]) -> str:
    """Serialise the per-dim score map for the ``per_dim_scores`` column.

    ``sort_keys=True`` so two rounds with the same dimensions write
    byte-identical text; that's nice for git-friendly DB diffs and for any
    later content-addressed dedup.
    """
    return json.dumps(scores, sort_keys=True, ensure_ascii=False)


def parse_per_dim_scores(value: str) -> dict[str, float]:
    """Inverse of :func:`serialize_per_dim_scores`."""
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError(f"per_dim_scores must be a JSON object, got {type(decoded).__name__}")
    return {str(k): float(v) for k, v in decoded.items()}


def serialize_downstream_status(status: dict[str, str] | None) -> str | None:
    """Serialise ``downstream_status`` — preserves ``None`` so the column stays NULL.

    The contract distinguishes three states:

    - ``None`` → SQL NULL → "not a pipeline node, no analysis applies"
    - ``"{}"`` → "analysed, but no downstream consumers"
    - ``"{node: status, ...}"`` → standard case
    """
    if status is None:
        return None
    return json.dumps(status, sort_keys=True, ensure_ascii=False)


def parse_downstream_status(value: str | None) -> dict[str, str] | None:
    """Inverse of :func:`serialize_downstream_status`."""
    if value is None:
        return None
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError(f"downstream_status must be a JSON object, got {type(decoded).__name__}")
    return {str(k): str(v) for k, v in decoded.items()}


# ---------------------------------------------------------------------------
# Row id (ULID) — local helper, callers should not need to mint these directly.
# ---------------------------------------------------------------------------


def _new_iteration_id() -> str:
    """Mint a ULID for a single iteration row.

    Same algorithm as :func:`new_session_id`; we keep two separately named
    callables so the API surface mirrors the design doc and future audits can
    grep for either ``new_session_id`` (returned to callers) or this
    private helper (never exposed).
    """
    return new_session_id()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def insert_iteration(
    conn: sqlite3.Connection,
    *,
    prompt_id: str,
    session_id: str,
    round: int,
    is_baseline: bool,
    parent_version: int | None,
    new_version: int | None,
    revise_mode: ReviseMode | None,
    revise_instruction: str | None,
    critique_text: str | None,
    weighted_score: float,
    per_dim_scores: dict[str, float],
    downstream_status: dict[str, str] | None,
    converged_reason: ConvergedReason | None,
    started_at: datetime,
    finished_at: datetime | None,
) -> str:
    """Insert a new row into ``iterations`` and return its ULID.

    All keyword-only to match the DDL field order and avoid argument-order
    bugs at the call site (15+ columns is too many for positional). The id
    is generated server-side rather than accepted as a parameter so the
    caller can't accidentally collide two rounds.
    """
    iteration_id = _new_iteration_id()
    conn.execute(
        """
        INSERT INTO iterations
            (id, prompt_id, round, session_id, is_baseline,
             parent_version, new_version, revise_mode, revise_instruction,
             critique_text, weighted_score, per_dim_scores,
             downstream_status, converged_reason, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            iteration_id,
            prompt_id,
            round,
            session_id,
            1 if is_baseline else 0,
            parent_version,
            new_version,
            revise_mode,
            revise_instruction,
            critique_text,
            weighted_score,
            serialize_per_dim_scores(per_dim_scores),
            serialize_downstream_status(downstream_status),
            converged_reason,
            _isoformat(started_at),
            _isoformat(finished_at) if finished_at is not None else None,
        ),
    )
    return iteration_id


def update_downstream_status(
    conn: sqlite3.Connection,
    iteration_id: str,
    node_id: str,
    status: DownstreamStatus,
) -> None:
    """Merge ``{node_id: status}`` into the row's ``downstream_status`` JSON.

    Read-modify-write inside a single connection — callers needing
    cross-connection safety should wrap the call in
    :func:`~aitap.store.db.transaction` with ``immediate=True``.

    Raises :class:`KeyError` when the row doesn't exist so accidental
    mis-id'd updates surface immediately instead of silently doing nothing.
    """
    cur = conn.execute(
        "SELECT downstream_status FROM iterations WHERE id = ?",
        (iteration_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"no iteration row with id {iteration_id!r}")
    current = parse_downstream_status(row["downstream_status"]) or {}
    current[node_id] = status
    conn.execute(
        "UPDATE iterations SET downstream_status = ? WHERE id = ?",
        (serialize_downstream_status(current), iteration_id),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def read_session(conn: sqlite3.Connection, session_id: str) -> list[Iteration]:
    """Return every iteration row in *session_id* ordered by ``round`` ascending.

    The round ordering matters for the UI: baseline (round 1) at the top,
    then revise rounds in chronological order so the "score over time"
    sparkline reads left-to-right naturally.
    """
    cur = conn.execute(
        "SELECT * FROM iterations WHERE session_id = ? ORDER BY round ASC",
        (session_id,),
    )
    return [_row_to_iteration(row) for row in cur.fetchall()]


def latest_iteration_for(conn: sqlite3.Connection, prompt_id: str) -> Iteration | None:
    """Return the most recently *started* iteration for *prompt_id*, or ``None``.

    "Most recent" is defined by ``started_at DESC`` because a long-running
    revise round may finish after a shorter follow-up was queued; the
    caller's intent is usually "what's the freshest signal we have" which
    maps to start time. Ties broken by ``id DESC`` (ULIDs are sortable).
    """
    cur = conn.execute(
        """
        SELECT * FROM iterations
        WHERE prompt_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (prompt_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_iteration(row)


def read_iterations_for(
    conn: sqlite3.Connection,
    prompt_id: str,
    *,
    limit: int = 50,
) -> list[Iteration]:
    """Return iterations for *prompt_id*, newest first, capped at *limit*.

    Used by the prompt-detail history view which interleaves
    :func:`~aitap.store.history.read_versions` and these rows on the same
    timeline. ``limit`` is a soft cap; the UI fetches additional pages on
    scroll.
    """
    cur = conn.execute(
        """
        SELECT * FROM iterations
        WHERE prompt_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (prompt_id, limit),
    )
    return [_row_to_iteration(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _row_to_iteration(row: sqlite3.Row) -> Iteration:
    """Parse a raw row into an :class:`Iteration`.

    Centralised so changes to the column set ripple to exactly one place
    rather than four read helpers.
    """
    finished_raw = row["finished_at"]
    return Iteration(
        id=cast(str, row["id"]),
        prompt_id=cast(str, row["prompt_id"]),
        round=int(row["round"]),
        session_id=cast(str, row["session_id"]),
        is_baseline=bool(row["is_baseline"]),
        parent_version=row["parent_version"],
        new_version=row["new_version"],
        revise_mode=cast("ReviseMode | None", row["revise_mode"]),
        revise_instruction=row["revise_instruction"],
        critique_text=row["critique_text"],
        weighted_score=float(row["weighted_score"]),
        per_dim_scores=parse_per_dim_scores(cast(str, row["per_dim_scores"])),
        downstream_status=parse_downstream_status(row["downstream_status"]),
        converged_reason=cast("ConvergedReason | None", row["converged_reason"]),
        started_at=datetime.fromisoformat(cast(str, row["started_at"])),
        finished_at=(
            datetime.fromisoformat(cast(str, finished_raw)) if finished_raw is not None else None
        ),
    )


def _isoformat(value: datetime) -> str:
    """Normalise datetimes to ISO-8601 text for the TIMESTAMP columns.

    SQLite has no native datetime type — TIMESTAMP is stored as TEXT.
    We always write tz-aware ISO-8601 (or, when the caller passes a naive
    value, leave it naive so the round-trip preserves what they gave us).
    """
    if value.tzinfo is None:
        return value.isoformat()
    # Normalise to UTC so two writers in different zones produce the same
    # text for the same instant.
    return value.astimezone(timezone.utc).isoformat()
