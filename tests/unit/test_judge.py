"""Unit tests for :mod:`aitap.iterate.judge` — Wave 4 LLM-as-judge.

All LLM traffic is routed through :class:`MockLLMClient` so these tests
are offline by construction. The judge module is intentionally provider-
agnostic; it only consumes the :class:`LLMClient` abstract contract.

Coverage matrix:

- ``score_outputs`` happy path with the default 4-dim rubric.
- A judge response missing one dimension still produces a valid
  :class:`JudgeScore` with the missing dim defaulted to ``0.0``.
- An entire judge response that fails to parse falls back to a zero
  :class:`JudgeScore` for that case alone — siblings are unaffected.
- Empty ``outputs`` short-circuits to ``[]`` without invoking the LLM.
- Custom dimensions weight-sum into the correct ``weighted_total``.
- ``client.chat`` is invoked exactly once per case (no batching).
- :func:`load_dimensions` honours the three-layer override:
    default -> project-level config.yaml -> per-prompt yaml.
- A ``reference`` ideal answer (when provided) is grounded into the
  user-side judge prompt so the model sees the gold output.
- The judge's free-text ``critique`` is propagated onto each
  :class:`JudgeScore`.
- :func:`persist_judge_scores` writes one ``scores`` row per case under
  ``judge_kind='llm'`` so downstream analytics can join on the existing
  schema without an alter-table.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from aitap.config import Settings
from aitap.deep.testing import MockLLMClient
from aitap.iterate.judge import (
    Dimension,
    JudgeScore,
    load_dimensions,
    persist_judge_scores,
    score_outputs,
)
from aitap.iterate.judge_defaults import DEFAULT_DIMENSIONS
from aitap.store import db as store_db
from aitap.store import runs as runs_dao

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _judge_reply(
    *,
    accuracy: float = 0.8,
    relevance: float = 0.7,
    safety: float = 1.0,
    format_: float = 1.0,
    critique: str = "looks fine",
) -> str:
    return json.dumps(
        {
            "accuracy": accuracy,
            "relevance": relevance,
            "safety": safety,
            "format": format_,
            "critique": critique,
        }
    )


def _sample_outputs(n: int = 3) -> list[dict[str, Any]]:
    """Three dict-shaped outputs as if loaded from the JSONL sidecar.

    We keep the forward-looking ``cost_usd`` / ``usage`` / ``latency_ms``
    extras around so the judge module proves it does not strip them — a
    downstream critic still needs them to target the most expensive cases.
    """
    base: list[dict[str, Any]] = []
    for i in range(n):
        base.append(
            {
                "case_index": i,
                "text": f"answer-{i}",
                "image_path": None,
                "error": None,
                "intermediate": None,
                "cost_usd": 0.0002,
                "usage": {"input_tokens": 12, "output_tokens": 5},
                "latency_ms": None,
            }
        )
    return base


# ---------------------------------------------------------------------------
# score_outputs — happy path
# ---------------------------------------------------------------------------


async def test_score_outputs_happy_path_returns_one_score_per_case() -> None:
    outputs = _sample_outputs(3)
    client = MockLLMClient(
        scripted=[_judge_reply() for _ in outputs],
    )
    scores = await score_outputs(
        site_purpose="Summarises customer support emails.",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    assert len(scores) == 3
    assert all(isinstance(s, JudgeScore) for s in scores)
    # All four default dims surface on every score.
    for s in scores:
        assert set(s.per_dim) == {"accuracy", "relevance", "safety", "format"}
    # weighted_total reflects the default weights (0.4/0.3/0.15/0.15)
    # against the constant 0.8/0.7/1.0/1.0 reply:
    # 0.8*0.4 + 0.7*0.3 + 1.0*0.15 + 1.0*0.15 = 0.32 + 0.21 + 0.15 + 0.15 = 0.83
    for s in scores:
        assert s.weighted_total == pytest.approx(0.83, abs=1e-6)
    assert all(s.critique == "looks fine" for s in scores)


async def test_score_outputs_invokes_client_once_per_case() -> None:
    outputs = _sample_outputs(4)
    client = MockLLMClient(scripted=[_judge_reply() for _ in outputs])
    await score_outputs(
        site_purpose="any",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    assert len(client.calls) == 4


async def test_score_outputs_uses_llm_client_abstract_only() -> None:
    """The judge module must not import provider SDKs directly.

    We assert this structurally by feeding a MockLLMClient (which has
    ``provider_name == "mock"``) and confirming the call goes through
    cleanly. The real safeguard is the absence of any
    ``aitap.deep.<provider>_client`` import in judge.py.
    """
    outputs = _sample_outputs(1)
    client = MockLLMClient(scripted=[_judge_reply()])
    scores = await score_outputs(
        site_purpose="any",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    assert len(scores) == 1
    assert client.provider_name == "mock"


# ---------------------------------------------------------------------------
# Resilience — missing dim / non-JSON / empty outputs
# ---------------------------------------------------------------------------


async def test_score_outputs_missing_dimension_defaults_to_zero() -> None:
    """Judge omitted 'safety' — weighted_total must still compute without raising."""
    partial = json.dumps(
        {
            "accuracy": 1.0,
            "relevance": 1.0,
            # safety missing on purpose
            "format": 1.0,
            "critique": "skipped safety",
        }
    )
    outputs = _sample_outputs(1)
    client = MockLLMClient(scripted=[partial])
    scores = await score_outputs(
        site_purpose="any",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    assert len(scores) == 1
    s = scores[0]
    assert s.per_dim["safety"] == 0.0
    # 1.0*0.4 + 1.0*0.3 + 0.0*0.15 + 1.0*0.15 = 0.85
    assert s.weighted_total == pytest.approx(0.85, abs=1e-6)


async def test_score_outputs_unparseable_response_yields_zero_score(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A single malformed JSON reply must not poison sibling cases."""
    outputs = _sample_outputs(3)
    client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.9, relevance=0.9, safety=1.0, format_=1.0),
            "this is not json at all",
            _judge_reply(accuracy=0.5, relevance=0.5, safety=0.5, format_=0.5),
        ]
    )
    with caplog.at_level("WARNING"):
        scores = await score_outputs(
            site_purpose="any",
            outputs=outputs,
            dimensions=DEFAULT_DIMENSIONS,
            client=client,
        )
    assert len(scores) == 3
    # Case 1 collapses to all-zero; weighted_total == 0.
    assert scores[1].weighted_total == 0.0
    assert all(v == 0.0 for v in scores[1].per_dim.values())
    # Case 0 + 2 are intact.
    assert scores[0].weighted_total > 0.0
    assert scores[2].weighted_total == pytest.approx(0.5, abs=1e-6)
    # We log a warning so operators can grep for "judge parse failure".
    assert any("judge" in rec.message.lower() for rec in caplog.records)


async def test_score_outputs_empty_outputs_short_circuits() -> None:
    client = MockLLMClient(scripted=[])
    scores = await score_outputs(
        site_purpose="any",
        outputs=[],
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    assert scores == []
    assert client.calls == []  # zero LLM calls — no spend on nothing


# ---------------------------------------------------------------------------
# Custom dimensions
# ---------------------------------------------------------------------------


async def test_score_outputs_custom_dimensions_weight_sum() -> None:
    """Two custom dimensions with weight 0.5 each → weighted_total = mean."""
    dims = [
        Dimension(name="alpha", weight=0.5, rubric="alpha rubric"),
        Dimension(name="beta", weight=0.5, rubric="beta rubric"),
    ]
    outputs = _sample_outputs(1)
    reply = json.dumps({"alpha": 0.4, "beta": 0.8, "critique": "ok"})
    client = MockLLMClient(scripted=[reply])
    scores = await score_outputs(
        site_purpose="any",
        outputs=outputs,
        dimensions=dims,
        client=client,
    )
    assert scores[0].per_dim == {"alpha": 0.4, "beta": 0.8}
    assert scores[0].weighted_total == pytest.approx(0.6, abs=1e-6)


async def test_score_outputs_ignores_extra_dims_from_judge() -> None:
    """Judge invented a dimension we did not ask for — silently dropped."""
    dims = [Dimension(name="accuracy", weight=1.0, rubric="acc rubric")]
    outputs = _sample_outputs(1)
    reply = json.dumps({"accuracy": 0.9, "invented": 0.1, "critique": "x"})
    client = MockLLMClient(scripted=[reply])
    scores = await score_outputs(
        site_purpose="any",
        outputs=outputs,
        dimensions=dims,
        client=client,
    )
    assert scores[0].per_dim == {"accuracy": 0.9}
    assert scores[0].weighted_total == pytest.approx(0.9, abs=1e-6)


# ---------------------------------------------------------------------------
# Reference grounding (ideal answer)
# ---------------------------------------------------------------------------


async def test_score_outputs_reference_appears_in_user_prompt() -> None:
    outputs = _sample_outputs(1)
    client = MockLLMClient(scripted=[_judge_reply()])
    await score_outputs(
        site_purpose="Summarises emails.",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
        reference={"ideal": "the gold answer string"},
    )
    # The user-side prompt is the second message (system is first).
    assert len(client.calls) == 1
    user_content = client.calls[0].messages[1].content
    assert "the gold answer string" in user_content


async def test_score_outputs_site_purpose_appears_in_user_prompt() -> None:
    outputs = _sample_outputs(1)
    client = MockLLMClient(scripted=[_judge_reply()])
    await score_outputs(
        site_purpose="Summarises emails in one sentence.",
        outputs=outputs,
        dimensions=DEFAULT_DIMENSIONS,
        client=client,
    )
    user_content = client.calls[0].messages[1].content
    assert "Summarises emails in one sentence." in user_content


# ---------------------------------------------------------------------------
# load_dimensions — three-layer override
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    """Build a Settings instance rooted in *tmp_path* with .aitap subdirs."""
    (tmp_path / ".aitap" / "prompts").mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


def test_load_dimensions_default_when_no_overrides(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    dims = load_dimensions(settings, prompt_id=None)
    assert dims == DEFAULT_DIMENSIONS


def test_load_dimensions_project_level_override(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    (settings.project_root / ".aitap" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "judge": {
                    "dimensions": [
                        {"name": "citations", "weight": 0.6, "rubric": "Cite sources"},
                        {"name": "tone", "weight": 0.4, "rubric": "Warm and clear"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    dims = load_dimensions(settings, prompt_id=None)
    assert [d.name for d in dims] == ["citations", "tone"]
    assert dims[0].weight == pytest.approx(0.6)


def test_load_dimensions_per_prompt_overrides_project(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    # Project-level overrides
    (settings.project_root / ".aitap" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "judge": {
                    "dimensions": [
                        {"name": "citations", "weight": 1.0, "rubric": "Cite sources"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    # Per-prompt: stronger override
    prompt_yaml = settings.project_root / ".aitap" / "prompts" / "p-1.prompt.yaml"
    prompt_yaml.write_text(
        yaml.safe_dump(
            {
                "judge_dimensions": [
                    {"name": "sql_validity", "weight": 0.7, "rubric": "Valid SQL"},
                    {"name": "accuracy", "weight": 0.3, "rubric": "Correct logic"},
                ]
            }
        ),
        encoding="utf-8",
    )
    dims = load_dimensions(settings, prompt_id="p-1")
    assert [d.name for d in dims] == ["sql_validity", "accuracy"]


def test_load_dimensions_per_prompt_unknown_id_falls_back_to_project(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    (settings.project_root / ".aitap" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "judge": {
                    "dimensions": [
                        {"name": "citations", "weight": 1.0, "rubric": "Cite sources"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    dims = load_dimensions(settings, prompt_id="does-not-exist")
    assert [d.name for d in dims] == ["citations"]


def test_load_dimensions_malformed_yaml_falls_back_to_default(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    (settings.project_root / ".aitap" / "config.yaml").write_text(
        "judge:\n  dimensions:\n    - not-a-mapping\n",
        encoding="utf-8",
    )
    dims = load_dimensions(settings, prompt_id=None)
    # Bad config must not crash — defaults are the safe fallback.
    assert dims == DEFAULT_DIMENSIONS


# ---------------------------------------------------------------------------
# persist_judge_scores — writes one scores row per case
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "db.sqlite"
    conn = store_db.connect(db_path)
    store_db.init_db(conn)
    return conn


def _seed_run(conn: sqlite3.Connection, run_id: str = "run-1") -> None:
    """Insert the minimum scaffolding for the scores FK to hold."""
    runs_dao.insert_run(
        conn,
        run_id=run_id,
        target_kind="prompt",
        target_id="p-1",
        target_version=1,
        provider="anthropic",
        model="claude-sonnet-4-6",
        parameters_json="{}",
    )


def test_persist_judge_scores_writes_one_row_per_case(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    scores = [
        JudgeScore(
            weighted_total=0.8,
            per_dim={"accuracy": 0.9, "relevance": 0.7},
            critique="case 0 critique",
        ),
        JudgeScore(
            weighted_total=0.6,
            per_dim={"accuracy": 0.5, "relevance": 0.7},
            critique="case 1 critique",
        ),
    ]
    persist_judge_scores(
        conn,
        run_id="run-1",
        scores=scores,
        judge_name="claude-sonnet-4-6",
    )
    rows = runs_dao.read_scores(conn, "run-1")
    assert len(rows) == 2
    by_idx = {int(r["case_index"]): r for r in rows}
    assert by_idx[0]["judge_kind"] == "llm"
    assert by_idx[0]["judge_name"] == "claude-sonnet-4-6"
    assert by_idx[0]["score"] == pytest.approx(0.8)
    assert by_idx[1]["score"] == pytest.approx(0.6)
    # Critique flows into the rationale column so a human reader can see it.
    assert "case 0" in (by_idx[0]["rationale"] or "")


def test_persist_judge_scores_empty_list_is_a_noop(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    persist_judge_scores(
        conn,
        run_id="run-1",
        scores=[],
        judge_name="any-model",
    )
    assert runs_dao.read_scores(conn, "run-1") == []
