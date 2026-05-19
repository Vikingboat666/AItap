"""Tests for ``store/iterations.py`` — Wave 4 iteration log DAO.

The iteration log is an event-stream table keyed on (prompt_id, session_id,
round). Each /iterate invocation produces one ``session_id`` and a series of
``round`` rows under it; convergence + downstream-status updates land in the
same rows. These tests exercise the DAO surface from the design doc plus the
two index-and-table assertions the migration must satisfy.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
)
from aitap.store import db, iterations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path):
    """Open + init a fresh DB and seed two prompts so FK targets exist."""
    c = db.connect(tmp_path / "db.sqlite")
    db.init_db(c)
    # Seed prompts so iterations.prompt_id FK is satisfiable.
    for pid in ("p-alpha", "p-beta"):
        db.upsert_prompt(
            c,
            PromptSite(
                id=pid,
                name=pid,
                provider=Provider.OPENAI,
                location=CodeLocation(file="x.py", line_start=1, line_end=2),
                messages=[Message(role=Role.USER, template_text="hi")],
                parameters=CallParameters(model="gpt-4o"),
                confidence=Confidence.HIGH,
            ),
        )
    try:
        yield c
    finally:
        c.close()


def _now(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def _insert(
    conn: sqlite3.Connection,
    *,
    prompt_id: str = "p-alpha",
    session_id: str | None = None,
    round_: int = 1,
    is_baseline: bool = True,
    parent_version: int | None = None,
    new_version: int | None = None,
    revise_mode: iterations.ReviseMode | None = None,
    revise_instruction: str | None = None,
    critique_text: str | None = None,
    weighted_score: float = 0.5,
    per_dim_scores: dict[str, float] | None = None,
    downstream_status: dict[str, str] | None = None,
    converged_reason: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> str:
    sid = session_id if session_id is not None else iterations.new_session_id()
    return iterations.insert_iteration(
        conn,
        prompt_id=prompt_id,
        session_id=sid,
        round=round_,
        is_baseline=is_baseline,
        parent_version=parent_version,
        new_version=new_version,
        revise_mode=revise_mode,
        revise_instruction=revise_instruction,
        critique_text=critique_text,
        weighted_score=weighted_score,
        per_dim_scores=per_dim_scores if per_dim_scores is not None else {"accuracy": 0.5},
        downstream_status=downstream_status,
        converged_reason=converged_reason,
        started_at=started_at if started_at is not None else _now(),
        finished_at=finished_at,
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_init_db_creates_iterations_table(conn: sqlite3.Connection) -> None:
    """Fresh init_db must create the iterations table."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='iterations'")
    assert cur.fetchone() is not None


def test_init_db_creates_iterations_indexes(conn: sqlite3.Connection) -> None:
    """Both prompt-scoped and session-scoped indexes must exist."""
    cur = conn.execute("PRAGMA index_list('iterations')")
    index_names = {row["name"] for row in cur.fetchall()}
    assert "idx_iterations_prompt" in index_names
    assert "idx_iterations_session" in index_names


def test_init_db_iterations_is_idempotent(tmp_path: Path) -> None:
    """Calling init_db twice on the same file must not raise or duplicate the table."""
    path = tmp_path / "db.sqlite"
    c = db.connect(path)
    db.init_db(c)
    db.init_db(c)  # must not raise
    cur = c.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='iterations'"
    )
    assert cur.fetchone()["n"] == 1
    c.close()


def test_iterations_columns_match_design(conn: sqlite3.Connection) -> None:
    """Spot-check the column set so a future schema drift surfaces here."""
    cur = conn.execute("PRAGMA table_info('iterations')")
    columns = {row["name"] for row in cur.fetchall()}
    expected = {
        "id",
        "prompt_id",
        "round",
        "session_id",
        "is_baseline",
        "parent_version",
        "new_version",
        "revise_mode",
        "revise_instruction",
        "critique_text",
        "weighted_score",
        "per_dim_scores",
        "downstream_status",
        "converged_reason",
        "started_at",
        "finished_at",
    }
    assert expected.issubset(columns)


# ---------------------------------------------------------------------------
# new_session_id
# ---------------------------------------------------------------------------


def test_new_session_id_is_unique() -> None:
    """Two calls must produce different ids; trivial but load-bearing for the
    session column."""
    s1 = iterations.new_session_id()
    s2 = iterations.new_session_id()
    assert s1 != s2


def test_new_session_id_shape() -> None:
    """ULID-shape: 26 chars, Crockford base32 alphabet (no I, L, O, U)."""
    sid = iterations.new_session_id()
    assert isinstance(sid, str)
    assert len(sid) == 26
    allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert set(sid).issubset(allowed), f"unexpected chars in {sid!r}"


# ---------------------------------------------------------------------------
# insert_iteration
# ---------------------------------------------------------------------------


def test_insert_iteration_baseline_happy_path(conn: sqlite3.Connection) -> None:
    """Baseline rows have revise_mode=None, is_baseline=True, parent_version=None."""
    iter_id = _insert(
        conn,
        is_baseline=True,
        round_=1,
        weighted_score=0.62,
        per_dim_scores={"accuracy": 0.7, "format": 0.5},
    )
    row = conn.execute("SELECT * FROM iterations WHERE id = ?", (iter_id,)).fetchone()
    assert row is not None
    assert row["is_baseline"] == 1
    assert row["round"] == 1
    assert row["revise_mode"] is None
    assert row["weighted_score"] == pytest.approx(0.62)
    assert iterations.parse_per_dim_scores(row["per_dim_scores"]) == {
        "accuracy": 0.7,
        "format": 0.5,
    }


def test_insert_iteration_revise_happy_path(conn: sqlite3.Connection) -> None:
    """A guided-revise row carries mode + instruction + new_version."""
    sid = iterations.new_session_id()
    _insert(conn, session_id=sid, round_=1, is_baseline=True)
    iter_id = _insert(
        conn,
        session_id=sid,
        round_=2,
        is_baseline=False,
        parent_version=1,
        new_version=2,
        revise_mode="guided",
        revise_instruction="make tone professional",
        critique_text="too casual",
        weighted_score=0.78,
        started_at=_now(60),
    )
    row = conn.execute("SELECT * FROM iterations WHERE id = ?", (iter_id,)).fetchone()
    assert row["is_baseline"] == 0
    assert row["revise_mode"] == "guided"
    assert row["revise_instruction"] == "make tone professional"
    assert row["critique_text"] == "too casual"
    assert row["parent_version"] == 1
    assert row["new_version"] == 2


def test_insert_iteration_returns_unique_ulid(conn: sqlite3.Connection) -> None:
    """Each insert returns a fresh 26-char id."""
    a = _insert(conn, round_=1)
    b = _insert(conn, prompt_id="p-beta", round_=1)
    assert a != b
    assert len(a) == 26
    assert len(b) == 26


def test_insert_iteration_unique_constraint(conn: sqlite3.Connection) -> None:
    """(prompt_id, session_id, round) is unique — second insert raises IntegrityError."""
    sid = iterations.new_session_id()
    _insert(conn, session_id=sid, round_=1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, session_id=sid, round_=1)


def test_insert_iteration_persists_downstream_status(conn: sqlite3.Connection) -> None:
    """downstream_status survives the JSON round trip."""
    iter_id = _insert(
        conn,
        downstream_status={"draft": "unverified", "polish": "unverified"},
    )
    row = conn.execute(
        "SELECT downstream_status FROM iterations WHERE id = ?", (iter_id,)
    ).fetchone()
    assert iterations.parse_downstream_status(row["downstream_status"]) == {
        "draft": "unverified",
        "polish": "unverified",
    }


# ---------------------------------------------------------------------------
# update_downstream_status
# ---------------------------------------------------------------------------


def test_update_downstream_status_initialises_from_null(conn: sqlite3.Connection) -> None:
    """When the row was inserted with NULL downstream_status, the first update
    creates a fresh dict with just that key."""
    iter_id = _insert(conn, downstream_status=None)
    iterations.update_downstream_status(conn, iter_id, "draft", "verified")
    row = conn.execute(
        "SELECT downstream_status FROM iterations WHERE id = ?", (iter_id,)
    ).fetchone()
    assert iterations.parse_downstream_status(row["downstream_status"]) == {"draft": "verified"}


def test_update_downstream_status_adds_a_new_key(conn: sqlite3.Connection) -> None:
    """Updating a previously-absent node id merges into the existing dict."""
    iter_id = _insert(conn, downstream_status={"draft": "unverified"})
    iterations.update_downstream_status(conn, iter_id, "polish", "regressed")
    row = conn.execute(
        "SELECT downstream_status FROM iterations WHERE id = ?", (iter_id,)
    ).fetchone()
    assert iterations.parse_downstream_status(row["downstream_status"]) == {
        "draft": "unverified",
        "polish": "regressed",
    }


def test_update_downstream_status_overwrites_existing_key(
    conn: sqlite3.Connection,
) -> None:
    """Re-updating an existing node id replaces the prior status."""
    iter_id = _insert(conn, downstream_status={"draft": "unverified"})
    iterations.update_downstream_status(conn, iter_id, "draft", "verified")
    row = conn.execute(
        "SELECT downstream_status FROM iterations WHERE id = ?", (iter_id,)
    ).fetchone()
    assert iterations.parse_downstream_status(row["downstream_status"]) == {"draft": "verified"}


# ---------------------------------------------------------------------------
# read_session
# ---------------------------------------------------------------------------


def test_read_session_returns_rounds_ascending(conn: sqlite3.Connection) -> None:
    """Rounds within a session come back round=1 first regardless of insert order."""
    sid = iterations.new_session_id()
    _insert(conn, session_id=sid, round_=2, is_baseline=False, started_at=_now(60))
    _insert(conn, session_id=sid, round_=1, is_baseline=True, started_at=_now())
    _insert(conn, session_id=sid, round_=3, is_baseline=False, started_at=_now(120))
    rows = iterations.read_session(conn, sid)
    assert [r.round for r in rows] == [1, 2, 3]


def test_read_session_isolates_sessions(conn: sqlite3.Connection) -> None:
    """A second session under the same prompt isn't returned by read_session."""
    s1 = iterations.new_session_id()
    s2 = iterations.new_session_id()
    _insert(conn, session_id=s1, round_=1)
    _insert(conn, session_id=s2, round_=1)
    assert len(iterations.read_session(conn, s1)) == 1
    assert len(iterations.read_session(conn, s2)) == 1


def test_read_session_returns_empty_for_unknown(conn: sqlite3.Connection) -> None:
    """Unknown session id returns an empty list, not an error."""
    assert iterations.read_session(conn, "no-such-session") == []


# ---------------------------------------------------------------------------
# latest_iteration_for
# ---------------------------------------------------------------------------


def test_latest_iteration_for_returns_most_recent(conn: sqlite3.Connection) -> None:
    """Picks the row with the highest started_at across all sessions of a prompt."""
    _insert(conn, prompt_id="p-alpha", round_=1, started_at=_now())
    latest_id = _insert(
        conn,
        prompt_id="p-alpha",
        round_=1,  # different session, so UNIQUE doesn't trip
        started_at=_now(120),
    )
    result = iterations.latest_iteration_for(conn, "p-alpha")
    assert result is not None
    assert result.id == latest_id


def test_latest_iteration_for_prompt_isolation(conn: sqlite3.Connection) -> None:
    """A later iteration on a different prompt does not leak into the answer."""
    a = _insert(conn, prompt_id="p-alpha", round_=1, started_at=_now())
    _insert(conn, prompt_id="p-beta", round_=1, started_at=_now(60))
    result = iterations.latest_iteration_for(conn, "p-alpha")
    assert result is not None
    assert result.id == a


def test_latest_iteration_for_returns_none_when_absent(conn: sqlite3.Connection) -> None:
    assert iterations.latest_iteration_for(conn, "p-alpha") is None


# ---------------------------------------------------------------------------
# read_iterations_for
# ---------------------------------------------------------------------------


def test_read_iterations_for_returns_empty_for_unknown(conn: sqlite3.Connection) -> None:
    assert iterations.read_iterations_for(conn, "p-alpha") == []


def test_read_iterations_for_respects_limit(conn: sqlite3.Connection) -> None:
    """Even when more rows exist, the limit caps the result size."""
    for i in range(5):
        _insert(
            conn,
            prompt_id="p-alpha",
            round_=1,
            started_at=_now(i),
        )
    rows = iterations.read_iterations_for(conn, "p-alpha", limit=3)
    assert len(rows) == 3


def test_read_iterations_for_prompt_isolation(conn: sqlite3.Connection) -> None:
    """Rows for a different prompt must not appear in the result."""
    _insert(conn, prompt_id="p-alpha", round_=1)
    _insert(conn, prompt_id="p-beta", round_=1)
    rows = iterations.read_iterations_for(conn, "p-alpha")
    assert len(rows) == 1
    assert rows[0].prompt_id == "p-alpha"


# ---------------------------------------------------------------------------
# JSON serialise / parse helpers
# ---------------------------------------------------------------------------


def test_serialize_per_dim_scores_roundtrip_basic() -> None:
    payload = {"accuracy": 0.8, "safety": 0.95}
    assert iterations.parse_per_dim_scores(iterations.serialize_per_dim_scores(payload)) == payload


def test_serialize_per_dim_scores_handles_empty_dict() -> None:
    assert iterations.parse_per_dim_scores(iterations.serialize_per_dim_scores({})) == {}


def test_serialize_downstream_status_roundtrip_none() -> None:
    """None must serialise to None (not the JSON string ``"null"``) so the
    column stays SQL NULL — the unverified-badge UI distinguishes NULL from
    {} (the latter means 'analysed, but no downstream nodes')."""
    assert iterations.serialize_downstream_status(None) is None
    assert iterations.parse_downstream_status(None) is None


def test_serialize_downstream_status_roundtrip_dict() -> None:
    payload = {"draft": "verified", "polish": "regressed"}
    serialized = iterations.serialize_downstream_status(payload)
    assert serialized is not None
    assert iterations.parse_downstream_status(serialized) == payload


def test_serialize_downstream_status_empty_dict_preserved() -> None:
    """Empty dict serialises to the JSON string ``{}`` so 'no downstream nodes'
    is distinguishable from 'not yet analysed' (NULL)."""
    serialized = iterations.serialize_downstream_status({})
    assert serialized == "{}"
    assert iterations.parse_downstream_status(serialized) == {}


# ---------------------------------------------------------------------------
# Iteration dataclass round trip
# ---------------------------------------------------------------------------


def test_iteration_model_roundtrips_from_row(conn: sqlite3.Connection) -> None:
    """The Iteration model parses every row column back into rich types."""
    sid = iterations.new_session_id()
    iter_id = _insert(
        conn,
        session_id=sid,
        round_=1,
        is_baseline=True,
        weighted_score=0.42,
        per_dim_scores={"accuracy": 0.4, "format": 0.44},
        downstream_status={"draft": "unverified"},
        started_at=_now(),
        finished_at=_now(30),
    )
    rows = iterations.read_session(conn, sid)
    assert len(rows) == 1
    item = rows[0]
    assert item.id == iter_id
    assert item.is_baseline is True
    assert item.weighted_score == pytest.approx(0.42)
    assert item.per_dim_scores == {"accuracy": 0.4, "format": 0.44}
    assert item.downstream_status == {"draft": "unverified"}
    assert item.session_id == sid
