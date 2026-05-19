"""DAO + schema initialisation tests for ``store/db.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
    Provider,
    ProviderEvidence,
    Role,
)
from aitap.store import db


@pytest.fixture()
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "db.sqlite")
    db.init_db(c)
    try:
        yield c
    finally:
        c.close()


def _make_site(prompt_id: str = "abc123", name: str = "summarize") -> PromptSite:
    return PromptSite(
        id=prompt_id,
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=10, line_end=12),
        messages=[Message(role=Role.USER, template_text="hi")],
        parameters=CallParameters(model="gpt-4o", temperature=0.2),
        confidence=Confidence.HIGH,
    )


def _make_pipeline(pipeline_id: str = "pipe1") -> Pipeline:
    return Pipeline(
        id=pipeline_id,
        name="content_workflow",
        nodes=[PipelineNode(prompt_id="a"), PipelineNode(prompt_id="b")],
        edges=[PipelineEdge(source="a", target="b", kind=EdgeKind.VARIABLE)],
        entry_points=["a"],
        exit_points=["b"],
    )


def test_init_db_creates_tables(conn) -> None:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row["name"] for row in cur.fetchall()}
    expected = {
        "feedback",
        "pipelines",
        "prompt_versions",
        "prompts",
        "providers_detected",
        "runs",
        "schema_version",
        "scores",
    }
    assert expected.issubset(tables)


def test_init_db_records_schema_version(conn) -> None:
    cur = conn.execute("SELECT MAX(version) AS v FROM schema_version")
    assert cur.fetchone()["v"] == db.SCHEMA_VERSION


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Calling init_db twice on the same connection must not duplicate rows or fail."""
    path = tmp_path / "db.sqlite"
    c = db.connect(path)
    db.init_db(c)
    db.init_db(c)
    cur = c.execute("SELECT COUNT(*) AS n FROM schema_version")
    # One schema_version row per migration step (1..SCHEMA_VERSION); a second
    # init detects we're already at target and adds nothing.
    assert cur.fetchone()["n"] == db.SCHEMA_VERSION
    c.close()


def test_upsert_prompt_inserts_new_row(conn) -> None:
    site = _make_site()
    db.upsert_prompt(conn, site, last_commit="deadbeef")
    rows = db.read_prompts(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == "abc123"
    assert rows[0]["name"] == "summarize"
    assert rows[0]["last_commit"] == "deadbeef"
    assert rows[0]["confidence"] == "high"


def test_upsert_prompt_is_idempotent_on_same_id(conn) -> None:
    site = _make_site()
    db.upsert_prompt(conn, site)
    db.upsert_prompt(conn, site, last_commit="newer")
    rows = db.read_prompts(conn)
    assert len(rows) == 1
    assert rows[0]["last_commit"] == "newer"


def test_upsert_prompt_updates_payload_on_conflict(conn) -> None:
    """Re-upserting with same id but different payload should overwrite the
    payload while preserving first_seen_at."""
    original = _make_site()
    db.upsert_prompt(conn, original)
    first_seen = db.read_prompts(conn)[0]["first_seen_at"]

    revised = _make_site()
    revised = revised.model_copy(update={"name": "summarize_v2"})
    db.upsert_prompt(conn, revised)

    rows = db.read_prompts(conn)
    assert len(rows) == 1
    assert rows[0]["name"] == "summarize_v2"
    # first_seen_at must be preserved across upsert
    assert rows[0]["first_seen_at"] == first_seen


def test_read_prompts_filters_by_name(conn) -> None:
    db.upsert_prompt(conn, _make_site("id1", "alpha"))
    db.upsert_prompt(conn, _make_site("id2", "beta"))
    matched = db.read_prompts(conn, name="alpha")
    assert len(matched) == 1
    assert matched[0]["id"] == "id1"


def test_upsert_pipeline_round_trip(conn) -> None:
    pipe = _make_pipeline()
    db.upsert_pipeline(conn, pipe, last_commit="abc")
    rows = db.read_pipelines(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == "pipe1"
    assert rows[0]["name"] == "content_workflow"


def test_record_provider_evidence_dedupes(conn) -> None:
    ev = ProviderEvidence(
        provider=Provider.ANTHROPIC,
        source="config",
        location=CodeLocation(file="config.yaml", line_start=3, line_end=3),
        key_var_name="ANTHROPIC_API_KEY",
    )
    db.record_provider_evidence(conn, "/proj", ev)
    db.record_provider_evidence(conn, "/proj", ev)
    cur = conn.execute("SELECT COUNT(*) AS n FROM providers_detected")
    assert cur.fetchone()["n"] == 1


def test_transaction_rolls_back_on_exception(conn) -> None:
    site = _make_site()
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction(conn):
            db.upsert_prompt(conn, site)
            raise RuntimeError("boom")
    assert db.read_prompts(conn) == []
