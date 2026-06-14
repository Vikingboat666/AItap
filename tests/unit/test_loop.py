"""Unit tests for :mod:`aitap.iterate.loop` — Wave 4 full iteration orchestrator.

The loop ties judge + critic + impact + iterations DAO into a single
``iterate_loop`` coroutine. These tests pin its behaviour end-to-end with
:class:`MockLLMClient` and a tmp-path SQLite database — no provider
networking, no real OS-level concurrency, but real persistence so the
"atomic round" semantics are exercised.

Coverage matrix:

- ``check_convergence`` helper: returns the right reason for each trigger
  (max_rounds / delta / stagnation / absolute), tolerates baseline-only,
  and exposes the multi-trigger priority decision.
- ``iterate_loop`` happy path: baseline + revise rounds, scores monotonic
  up to delta-from-baseline trigger.
- max_rounds path: scores never rise, loop terminates at the cap.
- stagnation path: stale rounds in a row trip the window.
- mode dispatch: ``manual`` does not invoke the critic LLM; ``guided``
  threads the user instruction into the critic; ``auto`` is the default.
- ``CriticError`` path: the loop records a failed-sentinel row and stops
  (does not silently continue with the old template).
- atomicity: a forced exception inside the per-round transaction leaves
  the iteration row absent and the connection's BEGIN unwound.
- LLM scheduling: judge + critic + dispatch calls all happen outside the
  SQLite write lock so a long-running LLM cannot block other writers.
- impact integration: when the prompt is part of a pipeline, the last
  iteration row carries the analyzed downstream status; when it isn't,
  ``downstream_status`` is left ``None`` (SQL NULL).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from aitap.config import Settings
from aitap.deep.testing import MockLLMClient
from aitap.iterate.loop import (
    ConvergenceConfig,
    IterationOutcome,
    check_convergence,
    iterate_loop,
)
from aitap.playground import dispatch
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
    Role,
    TemplateKind,
)
from aitap.store import db as store_db
from aitap.store import iterations as iterations_dao
from aitap.store import runs as runs_dao
from aitap.store.iterations import Iteration

# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


PROMPT_ID = "prompt-loop-1"
DATASET_ID = "loop-cases"


def _prompt_site(prompt_id: str = PROMPT_ID, name: str = "summarise_email") -> PromptSite:
    return PromptSite(
        id=prompt_id,
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=1, line_end=2),
        messages=[
            Message(
                role=Role.SYSTEM,
                template_text="You summarise emails.",
                template_kind=TemplateKind.LITERAL,
            )
        ],
        parameters=CallParameters(model="gpt-4o", temperature=0.0),
        confidence=Confidence.HIGH,
    )


@pytest.fixture()
def project(tmp_path: Path) -> Settings:
    """Project-rooted Settings with ``.aitap`` directories pre-created."""
    aitap_dir = tmp_path / ".aitap"
    for child in ("prompts", "pipelines", "datasets", "runs"):
        (aitap_dir / child).mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


def _open_conn(project: Settings) -> sqlite3.Connection:
    conn = store_db.connect(project.db_path)
    store_db.init_db(conn)
    return conn


def _seed_prompt(project: Settings, site: PromptSite) -> None:
    conn = _open_conn(project)
    try:
        store_db.upsert_prompt(conn, site)
        # Seed v1 prompt_versions so the loop has a baseline parent.
        runs_dao.insert_prompt_version(
            conn,
            prompt_id=site.id,
            version=1,
            template_json=json.dumps([m.model_dump(mode="json") for m in site.messages]),
            parameters_json=site.parameters.model_dump_json(),
            note="seed v1",
            created_by="human",
            parent_version=None,
        )
    finally:
        conn.close()


def _seed_pipeline(project: Settings, pipeline: Pipeline) -> None:
    conn = _open_conn(project)
    try:
        store_db.upsert_pipeline(conn, pipeline)
    finally:
        conn.close()


def _seed_dataset(project: Settings, n: int = 2) -> None:
    """Write a tiny JSONL dataset so the dispatch path runs end-to-end."""
    path = project.datasets_dir / f"{DATASET_ID}.cases.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for i in range(n):
            handle.write(json.dumps({"value": f"case-{i}"}) + "\n")


def _judge_reply(
    *,
    accuracy: float,
    relevance: float | None = None,
    safety: float = 1.0,
    format_: float = 1.0,
    critique: str = "ok",
) -> str:
    """Build a judge JSON reply matching the default 4-dim rubric."""
    rel = accuracy if relevance is None else relevance
    return json.dumps(
        {
            "accuracy": accuracy,
            "relevance": rel,
            "safety": safety,
            "format": format_,
            "critique": critique,
        }
    )


def _critic_reply(template: str, rationale: str = "tightened constraints") -> str:
    return json.dumps({"revised_template": template, "rationale": rationale})


@pytest.fixture()
def mock_runner_client() -> Iterator[MockLLMClient]:
    """Install a dispatch client factory that returns a deterministic mock.

    The runner only needs to *answer*; the judge is a separate client.
    A long ``scripted`` list ensures the runner never runs out of replies
    even when the loop fans out across multiple rounds * cases.
    """
    runner = MockLLMClient(
        model="mock-runner",
        scripted=["runner-output"] * 200,
    )
    dispatch.set_profile_client_factory(lambda settings, profile_id: runner)
    try:
        yield runner
    finally:
        dispatch.set_profile_client_factory(None)


# --------------------------------------------------------------------------- #
# check_convergence                                                           #
# --------------------------------------------------------------------------- #


def _iter(
    *,
    round_: int,
    is_baseline: bool,
    weighted_score: float,
    converged_reason: str | None = None,
) -> Iteration:
    """Build a minimal :class:`Iteration` for the convergence helper.

    Most fields are not read by ``check_convergence``; we seed sensible
    defaults so the test reads like a one-liner.
    """
    from datetime import datetime, timezone

    return Iteration(
        id=f"row-{round_}",
        prompt_id=PROMPT_ID,
        round=round_,
        session_id="sess-x",
        is_baseline=is_baseline,
        parent_version=None if is_baseline else 1,
        new_version=None if is_baseline else round_,
        revise_mode=None if is_baseline else "auto",
        revise_instruction=None,
        critique_text=None,
        weighted_score=weighted_score,
        per_dim_scores={"accuracy": weighted_score},
        downstream_status=None,
        converged_reason=converged_reason,  # type: ignore[arg-type]
        started_at=datetime(2026, 5, 20, 12, round_, 0, tzinfo=timezone.utc),
        finished_at=None,
    )


def test_check_convergence_returns_none_when_only_baseline() -> None:
    """A single baseline row never triggers any rule on its own."""
    cfg = ConvergenceConfig()
    assert (
        check_convergence(
            [_iter(round_=1, is_baseline=True, weighted_score=0.5)],
            cfg,
        )
        is None
    )


def test_check_convergence_max_rounds_triggers_at_cap() -> None:
    """At baseline + max_rounds revise rounds the loop must stop.

    We pick scores that oscillate above the stagnation epsilon so the
    ``stagnation`` rule does not pre-empt ``max_rounds``; only the hard
    round-count cap should fire.
    """
    cfg = ConvergenceConfig(
        max_rounds=3,
        delta_from_baseline=10.0,
        stagnation_window=99,  # off
    )
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.55),
        _iter(round_=3, is_baseline=False, weighted_score=0.51),
    ]
    assert check_convergence(rows, cfg) == "max_rounds"


def test_check_convergence_delta_uses_baseline_not_round_over_round() -> None:
    """Delta is ``current - baseline``; tiny round-over-round still trips when
    the cumulative gap exceeds the threshold."""
    cfg = ConvergenceConfig(delta_from_baseline=0.15)
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.55),
        _iter(round_=3, is_baseline=False, weighted_score=0.66),  # delta=0.16
    ]
    assert check_convergence(rows, cfg) == "delta"


def test_check_convergence_delta_does_not_trigger_below_threshold() -> None:
    cfg = ConvergenceConfig(delta_from_baseline=0.20, max_rounds=10)
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.55),
        _iter(round_=3, is_baseline=False, weighted_score=0.60),
    ]
    assert check_convergence(rows, cfg) is None


def test_check_convergence_stagnation_window_triggers() -> None:
    """``stagnation_window`` consecutive rounds with tiny round-over-round
    deltas trip the stop."""
    cfg = ConvergenceConfig(
        stagnation_window=3,
        stagnation_epsilon=0.02,
        delta_from_baseline=10.0,
        max_rounds=20,
    )
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.60),
        _iter(round_=2, is_baseline=False, weighted_score=0.78),
        _iter(round_=3, is_baseline=False, weighted_score=0.79),
        _iter(round_=4, is_baseline=False, weighted_score=0.79),
        _iter(round_=5, is_baseline=False, weighted_score=0.79),
    ]
    assert check_convergence(rows, cfg) == "stagnation"


def test_check_convergence_stagnation_window_not_triggered_below_window() -> None:
    """With only 2 stagnant rounds in a 3-round window we must keep going."""
    cfg = ConvergenceConfig(
        stagnation_window=3,
        stagnation_epsilon=0.02,
        delta_from_baseline=10.0,
        max_rounds=20,
    )
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.60),
        _iter(round_=2, is_baseline=False, weighted_score=0.78),
        _iter(round_=3, is_baseline=False, weighted_score=0.79),
    ]
    assert check_convergence(rows, cfg) is None


def test_check_convergence_absolute_threshold_off_by_default() -> None:
    """``absolute_threshold`` defaults to ``None`` and never triggers when unset."""
    cfg = ConvergenceConfig(max_rounds=10, delta_from_baseline=10.0)
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.99),
    ]
    assert check_convergence(rows, cfg) is None


def test_check_convergence_absolute_threshold_triggers_when_set() -> None:
    cfg = ConvergenceConfig(
        max_rounds=10,
        delta_from_baseline=10.0,
        absolute_threshold=0.90,
    )
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.91),
    ]
    assert check_convergence(rows, cfg) == "absolute"


def test_check_convergence_priority_delta_beats_max_rounds() -> None:
    """If both ``delta`` and ``max_rounds`` could fire, the "good outcome"
    (``delta``) wins so users see why iteration succeeded, not just that it
    timed out."""
    cfg = ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10)
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.65),
        _iter(round_=3, is_baseline=False, weighted_score=0.70),
    ]
    assert check_convergence(rows, cfg) == "delta"


def test_check_convergence_priority_delta_beats_absolute() -> None:
    cfg = ConvergenceConfig(
        max_rounds=20,
        delta_from_baseline=0.10,
        absolute_threshold=0.85,
    )
    rows = [
        _iter(round_=1, is_baseline=True, weighted_score=0.50),
        _iter(round_=2, is_baseline=False, weighted_score=0.92),  # both fire
    ]
    assert check_convergence(rows, cfg) == "delta"


# --------------------------------------------------------------------------- #
# iterate_loop — happy path                                                   #
# --------------------------------------------------------------------------- #


async def test_iterate_loop_baseline_then_delta_convergence(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """Scores rise enough to trigger the ``delta`` rule before max_rounds.

    The baseline round runs the prompt + judges, no critic. Subsequent
    rounds invoke the critic (auto mode), persist a new prompt_version,
    re-run, re-judge, and check convergence.
    """
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=2)

    # Two cases per round; the judge runs once per case.
    # Round 1 (baseline) at 0.50 weighted, round 2 at 0.82 -> delta=0.32 > 0.15.
    judge_client = MockLLMClient(
        scripted=[
            # baseline (2 cases)
            _judge_reply(accuracy=0.50, relevance=0.50, critique="weak start"),
            _judge_reply(accuracy=0.50, relevance=0.50, critique="weak start"),
            # round 2 (2 cases)
            _judge_reply(accuracy=0.82, relevance=0.82, critique="much better"),
            _judge_reply(accuracy=0.82, relevance=0.82, critique="much better"),
        ],
    )
    critic_client = MockLLMClient(
        scripted=[_critic_reply("REWRITTEN PROMPT", "tightened factual constraints")],
    )

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(max_rounds=5, delta_from_baseline=0.15),
    )

    assert isinstance(outcome, IterationOutcome)
    assert outcome.converged_reason == "delta"
    # baseline + 1 revise round before delta triggers
    assert len(outcome.iterations) == 2
    assert outcome.iterations[0].is_baseline is True
    assert outcome.iterations[1].is_baseline is False
    assert outcome.iterations[1].revise_mode == "auto"
    # The new prompt version was persisted.
    assert outcome.final_version >= 2


async def test_iterate_loop_max_rounds_termination(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """Scores stay flat at 0.50; loop walks baseline + 2 revise rounds and stops."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[_judge_reply(accuracy=0.50, critique="meh")] * 10,
    )
    critic_client = MockLLMClient(
        scripted=[_critic_reply(f"REV-{i}", "tweak") for i in range(10)],
    )

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(
            max_rounds=2,
            delta_from_baseline=10.0,  # effectively off
            stagnation_window=99,  # off
        ),
    )

    assert outcome.converged_reason == "max_rounds"
    # baseline + (max_rounds - 1) revise rounds == max_rounds total entries.
    assert len(outcome.iterations) == 2
    assert outcome.iterations[0].is_baseline is True
    assert outcome.iterations[-1].is_baseline is False


async def test_iterate_loop_stagnation_termination(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """Three consecutive stagnant rounds (round-over-round < epsilon) stop the loop."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    # baseline=0.60 -> r2=0.78 -> r3=0.79 -> r4=0.79 (stagnation triggers at round 4)
    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.60, critique="baseline"),
            _judge_reply(accuracy=0.78, critique="better"),
            _judge_reply(accuracy=0.79, critique="plateau"),
            _judge_reply(accuracy=0.79, critique="plateau"),
            _judge_reply(accuracy=0.79, critique="plateau"),
        ],
    )
    critic_client = MockLLMClient(
        scripted=[_critic_reply(f"REV-{i}", "minor tweak") for i in range(10)],
    )

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(
            max_rounds=10,
            delta_from_baseline=10.0,  # off
            stagnation_window=3,
            stagnation_epsilon=0.02,
        ),
    )

    assert outcome.converged_reason == "stagnation"


# --------------------------------------------------------------------------- #
# Mode dispatch                                                               #
# --------------------------------------------------------------------------- #


async def test_iterate_loop_manual_mode_uses_provided_text_no_critic_call(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """Manual mode: round 2's prompt text comes from ``manual_revisions``,
    the critic LLM is never invoked."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.50),  # baseline
            _judge_reply(accuracy=0.95),  # round 2 after manual edit
        ],
    )
    critic_client = MockLLMClient(scripted=[])  # critic must not be called

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        mode="manual",
        manual_revisions={2: "USER-EDITED PROMPT BODY"},
        convergence=ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10),
    )

    assert outcome.converged_reason in {"delta", "max_rounds"}
    assert critic_client.calls == [], "manual mode must not call critic LLM"
    # The persisted new version body should be the user-supplied text.
    conn = _open_conn(project)
    try:
        row = conn.execute(
            "SELECT template_json FROM prompt_versions WHERE prompt_id = ? AND version = ?",
            (site.id, outcome.final_version),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    # Manual text is stored verbatim as the system message body.
    assert "USER-EDITED PROMPT BODY" in row["template_json"]


async def test_iterate_loop_guided_mode_threads_instruction(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """Guided mode passes the user instruction to the critic LLM."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.50),  # baseline
            _judge_reply(accuracy=0.95),  # round 2
        ],
    )
    critic_client = MockLLMClient(
        scripted=[_critic_reply("PROFESSIONAL PROMPT", "switched register")]
    )

    instruction = "make the tone more professional"
    await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        mode="guided",
        instruction=instruction,
        convergence=ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10),
    )

    # The critic was called and saw the instruction.
    assert len(critic_client.calls) == 1
    user_msg = critic_client.calls[0].messages[-1].content
    assert instruction in user_msg


# --------------------------------------------------------------------------- #
# Critic failure handling                                                     #
# --------------------------------------------------------------------------- #


async def test_iterate_loop_critic_error_aborts_with_sentinel(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """When the critic LLM reply is unparseable, the loop must:

    - stop iterating (not loop forever on the same broken reply),
    - record a sentinel iteration row so the API/UI can surface ``failed``,
    - return a non-None ``converged_reason`` distinct from the normal three.
    """
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.50, critique="baseline"),
            # we'll never reach a second judge call because the critic fails
        ],
    )
    critic_client = MockLLMClient(
        scripted=["not parseable json at all"],
    )

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(max_rounds=5, delta_from_baseline=10.0),
    )

    # Loop stopped — did not march onward.
    assert outcome.converged_reason == "critic_failed"
    # baseline + 1 failed-sentinel row.
    assert len(outcome.iterations) == 2
    assert outcome.iterations[-1].revise_mode == "failed"


# --------------------------------------------------------------------------- #
# Atomicity                                                                   #
# --------------------------------------------------------------------------- #


async def test_iterate_loop_round_rollback_on_iteration_insert_failure(
    project: Settings,
    mock_runner_client: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``insert_iteration`` fails inside the per-round transaction, the
    new ``prompt_versions`` row in the same transaction must roll back too."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.50),  # baseline
            _judge_reply(accuracy=0.95),  # round 2
        ],
    )
    critic_client = MockLLMClient(scripted=[_critic_reply("REVISED", "x")])

    # Sabotage the iteration insert for the *second* call (after baseline)
    # so the new prompt_version write is forced to roll back.
    call_count = {"n": 0}
    real_insert = iterations_dao.insert_iteration

    def sabotaged_insert(*args: Any, **kwargs: Any) -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("forced failure")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(
        "aitap.iterate.loop.insert_iteration",
        sabotaged_insert,
    )

    with pytest.raises(RuntimeError, match="forced failure"):
        await iterate_loop(
            settings=project,
            prompt_id=site.id,
            dataset_id=DATASET_ID,
            client=mock_runner_client,
            judge_client=judge_client,
            critic_client=critic_client,
            convergence=ConvergenceConfig(max_rounds=3, delta_from_baseline=10.0),
        )

    # The new prompt_versions row that the failed round would have written
    # must have rolled back: only the baseline-seed v1 should exist.
    conn = _open_conn(project)
    try:
        versions = runs_dao.read_prompt_versions(conn, site.id)
    finally:
        conn.close()
    assert [v["version"] for v in versions] == [1]


async def test_iterate_loop_does_not_call_llm_inside_transaction(
    project: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge/critic/runner calls happen outside the SQLite write lock.

    We monkeypatch the transaction context-manager so we can flag any
    LLM call that is performed while the loop holds the write lock —
    the contract is that LLM I/O is never inside the transaction.
    """
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    # Mark whether we're inside a transaction at any LLM call moment.
    in_txn = {"flag": False}
    llm_inside: list[str] = []

    runner = MockLLMClient(model="mock-runner", scripted=["runner-output"] * 100)
    judge_client = MockLLMClient(
        scripted=[_judge_reply(accuracy=0.50), _judge_reply(accuracy=0.99)],
    )
    critic_client = MockLLMClient(
        scripted=[_critic_reply("R", "r")],
    )

    real_runner_chat = runner.chat
    real_judge_chat = judge_client.chat
    real_critic_chat = critic_client.chat

    async def spy_runner_chat(*a: Any, **kw: Any) -> Any:  # type: ignore[no-untyped-def]
        if in_txn["flag"]:
            llm_inside.append("runner")
        return await real_runner_chat(*a, **kw)

    async def spy_judge_chat(*a: Any, **kw: Any) -> Any:  # type: ignore[no-untyped-def]
        if in_txn["flag"]:
            llm_inside.append("judge")
        return await real_judge_chat(*a, **kw)

    async def spy_critic_chat(*a: Any, **kw: Any) -> Any:  # type: ignore[no-untyped-def]
        if in_txn["flag"]:
            llm_inside.append("critic")
        return await real_critic_chat(*a, **kw)

    monkeypatch.setattr(runner, "chat", spy_runner_chat)
    monkeypatch.setattr(judge_client, "chat", spy_judge_chat)
    monkeypatch.setattr(critic_client, "chat", spy_critic_chat)

    # Wrap the transaction CM so we can flip the in_txn flag.
    from contextlib import contextmanager

    real_transaction = store_db.transaction

    @contextmanager
    def spy_transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Any:
        in_txn["flag"] = True
        try:
            with real_transaction(conn, immediate=immediate) as c:
                yield c
        finally:
            in_txn["flag"] = False

    monkeypatch.setattr("aitap.iterate.loop.transaction", spy_transaction)

    dispatch.set_profile_client_factory(lambda settings, profile_id: runner)
    try:
        await iterate_loop(
            settings=project,
            prompt_id=site.id,
            dataset_id=DATASET_ID,
            client=runner,
            judge_client=judge_client,
            critic_client=critic_client,
            convergence=ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10),
        )
    finally:
        dispatch.set_profile_client_factory(None)

    assert llm_inside == [], f"LLM calls inside transaction: {llm_inside}"


# --------------------------------------------------------------------------- #
# Impact integration                                                          #
# --------------------------------------------------------------------------- #


async def test_iterate_loop_writes_downstream_status_when_in_pipeline(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """When the iterated prompt sits inside a pipeline, the final iteration
    row's ``downstream_status`` is populated with UNVERIFIED entries for
    every consumer."""
    site = _prompt_site()
    downstream = _prompt_site(prompt_id="prompt-downstream", name="downstream_node")
    _seed_prompt(project, site)
    _seed_prompt(project, downstream)
    pipeline = Pipeline(
        id="pipe-1",
        name="email_chain",
        nodes=[PipelineNode(prompt_id=site.id), PipelineNode(prompt_id=downstream.id)],
        edges=[PipelineEdge(source=site.id, target=downstream.id, kind=EdgeKind.VARIABLE)],
        entry_points=[site.id],
        exit_points=[downstream.id],
    )
    _seed_pipeline(project, pipeline)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[
            _judge_reply(accuracy=0.50),  # baseline
            _judge_reply(accuracy=0.90),  # round 2
        ],
    )
    critic_client = MockLLMClient(scripted=[_critic_reply("REV", "r")])

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10),
    )

    final = outcome.iterations[-1]
    assert final.downstream_status is not None
    assert downstream.id in final.downstream_status
    assert final.downstream_status[downstream.id] == "unverified"


async def test_iterate_loop_downstream_status_none_when_not_in_pipeline(
    project: Settings,
    mock_runner_client: MockLLMClient,
) -> None:
    """A prompt that's not a pipeline node leaves downstream_status as NULL."""
    site = _prompt_site()
    _seed_prompt(project, site)
    _seed_dataset(project, n=1)

    judge_client = MockLLMClient(
        scripted=[_judge_reply(accuracy=0.50), _judge_reply(accuracy=0.90)],
    )
    critic_client = MockLLMClient(scripted=[_critic_reply("REV", "r")])

    outcome = await iterate_loop(
        settings=project,
        prompt_id=site.id,
        dataset_id=DATASET_ID,
        client=mock_runner_client,
        judge_client=judge_client,
        critic_client=critic_client,
        convergence=ConvergenceConfig(max_rounds=2, delta_from_baseline=0.10),
    )

    final = outcome.iterations[-1]
    assert final.downstream_status is None


# --------------------------------------------------------------------------- #
# Backward compat                                                             #
# --------------------------------------------------------------------------- #


def test_iterate_one_round_still_exported() -> None:
    """The Wave 3 stub must still be importable for the api-iterate route."""
    from aitap.iterate import iterate_one_round  # noqa: F401


def test_iterate_loop_exported_from_package() -> None:
    """The new orchestrator is re-exported through the package root."""
    from aitap.iterate import ConvergenceConfig as PkgConvergenceConfig  # noqa: F401
    from aitap.iterate import LoopIterationOutcome as PkgIterationOutcome  # noqa: F401
    from aitap.iterate import iterate_loop as pkg_iterate_loop  # noqa: F401


# --------------------------------------------------------------------------- #
# Structural guarantees                                                       #
# --------------------------------------------------------------------------- #


def test_loop_module_does_not_import_provider_sdks() -> None:
    """Defence in depth — the orchestrator must only see LLMs via LLMClient."""
    here = Path(__file__).resolve().parents[2]
    loop_src = (here / "src" / "aitap" / "iterate" / "loop.py").read_text(encoding="utf-8")
    assert "import openai" not in loop_src
    assert "import anthropic" not in loop_src
    assert "from openai" not in loop_src
    assert "from anthropic" not in loop_src
