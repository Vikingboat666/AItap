"""Unit tests for the per-case outputs JSONL sidecar (Wave 4 prerequisite).

The sidecar lives at ``<runs_dir>/<run_id>/outputs.jsonl`` and is the
single source of truth the Wave 4 judge will consume to score each case.
These tests pin three independent contracts:

1. **Round-trip shape** — every line is a valid :class:`RunOutput` when
   passed through ``model_validate``. The reader in
   :mod:`aitap.server.routes.runs._load_outputs` MUST be able to consume
   what the writer produces without any conversion glue.
2. **Failure isolation** — a single case that errors inside the runner
   gets its own ``error``-filled row; cases that succeeded around it
   land alongside it unchanged. A judge can still score the survivors.
3. **Atomicity** — a crash mid-write leaves either no sidecar or a
   complete one, never a half-flushed file. We exercise this by
   monkey-patching :func:`os.replace` to raise after the tmp file is
   written and asserting the tmp gets cleaned up.

Pipeline mode is exercised here too: its rows have the contract
``intermediate`` field populated with the per-node trace, while the
forward-looking ``cost_usd`` / ``usage`` fields stay ``None`` because
:class:`PipelineRunResult` does not surface per-case ChatResponses
(by design — node-walk-level metrics roll up to the run, not the case).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from aitap.config import Settings
from aitap.deep.testing import MockLLMClient
from aitap.playground import dispatch
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
)
from aitap.scanner.models import EdgeKind as _EdgeKind
from aitap.server.routes import DatasetCase, RunCreate, RunOutput
from aitap.server.routes import runs as runs_route
from aitap.store import db as store_db
from aitap.store import runs as runs_dao

# ---------------------------------------------------------------------------
# Fixtures (mirror the shape of tests/unit/test_dispatch.py so a future
# refactor can hoist them into a shared conftest without churn)
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Settings:
    aitap_dir = tmp_path / ".aitap"
    for child in ("prompts", "pipelines", "datasets", "runs"):
        (aitap_dir / child).mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


@pytest.fixture()
def mock_client_factory() -> Iterator[MockLLMClient]:
    """Install a MockLLMClient as the dispatch adapter's factory.

    We script multiple replies so prompt-mode multi-case tests can
    assert distinct per-case ``text`` values land in the sidecar.
    """
    mock = MockLLMClient(
        model="mock-model",
        scripted=["reply-0", "reply-1", "reply-2", "reply-3"],
        default_reply="reply-default",
    )
    dispatch.set_client_factory(lambda provider, model: mock)
    try:
        yield mock
    finally:
        dispatch.set_client_factory(None)


@pytest.fixture()
def failing_client_factory() -> Iterator[MockLLMClient]:
    """Install a client whose ``chat`` always raises.

    Used to exercise the per-case ``error`` row: the runner traps the
    exception into ``RunOutput.error`` so the sidecar should record
    every case as a failure but still write the file.
    """

    class _AlwaysFailingClient(MockLLMClient):
        async def chat(self, *args: object, **kwargs: object) -> Any:
            raise RuntimeError("simulated provider outage")

    mock = _AlwaysFailingClient(model="mock-model")
    dispatch.set_client_factory(lambda provider, model: mock)
    try:
        yield mock
    finally:
        dispatch.set_client_factory(None)


def _open_conn(project: Settings) -> sqlite3.Connection:
    conn = store_db.connect(project.db_path)
    store_db.init_db(conn)
    return conn


def _prompt_site(prompt_id: str = "prompt-x") -> PromptSite:
    return PromptSite(
        id=prompt_id,
        name="echo_prompt",
        provider=Provider.ANTHROPIC,
        location=CodeLocation(file="x.py", line_start=1, line_end=1),
        messages=[
            Message(
                role=Role.USER,
                template_text="Echo: {value}",
                template_kind=TemplateKind.FSTRING,
            )
        ],
        parameters=CallParameters(temperature=0.0),
        confidence=Confidence.HIGH,
    )


def _seed_prompt_row(project: Settings, site: PromptSite) -> None:
    conn = _open_conn(project)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO prompts
                (id, name, provider, file, line_start, line_end,
                 confidence, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site.id,
                site.name,
                site.provider.value,
                site.location.file,
                site.location.line_start,
                site.location.line_end,
                site.confidence.value,
                site.model_dump_json(),
            ),
        )
    finally:
        conn.close()


def _seed_pipeline_row(project: Settings, pipeline: Pipeline) -> None:
    conn = _open_conn(project)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pipelines (id, name, payload_json) VALUES (?, ?, ?)",
            (pipeline.id, pipeline.name, pipeline.model_dump_json()),
        )
    finally:
        conn.close()


def _seed_run_row(
    project: Settings,
    run_id: str,
    target_id: str,
    *,
    target_kind: str = "prompt",
) -> None:
    conn = _open_conn(project)
    try:
        runs_dao.insert_run(
            conn,
            run_id=run_id,
            target_kind=target_kind,
            target_id=target_id,
            target_version=1,
            provider="anthropic",
            model="mock-model",
            parameters_json="{}",
        )
    finally:
        conn.close()


def _build_payload(
    *,
    target_kind: str = "prompt",
    target_id: str = "prompt-x",
    cases: list[DatasetCase] | None = None,
) -> RunCreate:
    return RunCreate(
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=target_id,
        target_version=1,
        cases=cases or [],
        provider=Provider.ANTHROPIC,
        model="mock-model",
        parameters=CallParameters(temperature=0.0),
    )


def _read_sidecar_lines(project: Settings, run_id: str) -> list[dict[str, Any]]:
    """Read the raw JSONL records (not RunOutput) so tests can assert extras.

    The extras (``cost_usd``, ``usage``, ``latency_ms``) are not part of
    the :class:`RunOutput` contract — pydantic drops them when
    constructing the response. To assert they were persisted we read the
    on-disk JSON directly.
    """
    path = dispatch.outputs_sidecar_path(project, run_id)
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Prompt-mode happy paths
# ---------------------------------------------------------------------------


def test_sidecar_written_with_one_line_per_case(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """Three cases produce three JSONL rows aligned by ``case_index``."""
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-sidecar-prompt"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[
            DatasetCase(inputs={"value": "a"}),
            DatasetCase(inputs={"value": "b"}),
            DatasetCase(inputs={"value": "c"}),
        ],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    records = _read_sidecar_lines(project, run_id)
    assert len(records) == 3
    # case_index ordering is stable and corresponds to dataset order.
    assert [r["case_index"] for r in records] == [0, 1, 2]
    # Each record carries the contract `text` field, populated from the
    # scripted MockLLMClient replies.
    assert [r["text"] for r in records] == ["reply-0", "reply-1", "reply-2"]
    # Errors are null on the happy path.
    assert all(r["error"] is None for r in records)


def test_sidecar_lines_roundtrip_into_RunOutput(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """Each JSONL row validates as a :class:`RunOutput` with zero conversion.

    This is the contract the reader (:func:`runs._load_outputs`) relies
    on; if it breaks, the API detail endpoint stops returning per-case
    output text.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-sidecar-roundtrip"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "hello"})],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    records = _read_sidecar_lines(project, run_id)
    assert len(records) == 1
    outputs = [RunOutput.model_validate(r) for r in records]
    assert outputs[0].case_index == 0
    assert outputs[0].text == "reply-0"
    assert outputs[0].error is None


def test_sidecar_records_per_case_cost_and_usage(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """The forward-looking judge fields land on each row in prompt mode.

    MockLLMClient reports cost_usd=0.0001 and (10, 10) tokens per call;
    we verify those numbers reach the sidecar so the Wave 4 judge can
    consume them without needing a runs-table round-trip.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-sidecar-metrics"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "x"})],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    records = _read_sidecar_lines(project, run_id)
    assert records[0]["cost_usd"] == pytest.approx(0.0001)
    assert records[0]["usage"] == {"input_tokens": 10, "output_tokens": 10}
    # Latency is reserved; not yet measured by the runner.
    assert records[0]["latency_ms"] is None


def test_load_outputs_reads_what_dispatch_wrote(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """``_load_outputs`` is the reader; it must consume the writer's output."""
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-roundtrip"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[
            DatasetCase(inputs={"value": "a"}),
            DatasetCase(inputs={"value": "b"}),
        ],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    outputs = runs_route._load_outputs(project, run_id)
    assert [o.case_index for o in outputs] == [0, 1]
    assert [o.text for o in outputs] == ["reply-0", "reply-1"]
    assert all(o.error is None for o in outputs)


# ---------------------------------------------------------------------------
# Pipeline-mode happy path
# ---------------------------------------------------------------------------


def test_sidecar_for_pipeline_end_to_end_carries_intermediate(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """Pipeline mode persists sink output in ``text`` + node trace in ``intermediate``.

    The contract decision here is that ``RunOutput.intermediate`` already
    carries the per-node text, so no separate intermediates.jsonl is
    needed. The forward-looking ``cost_usd`` / ``usage`` fields are null
    in pipeline mode (per-case ChatResponses aren't surfaced through
    :class:`PipelineRunResult`).
    """
    upstream = _prompt_site("p-up")
    downstream = _prompt_site("p-down")
    _seed_prompt_row(project, upstream)
    _seed_prompt_row(project, downstream)
    pipeline = Pipeline(
        id="pipe-sidecar",
        name="two_step",
        nodes=[
            PipelineNode(prompt_id=upstream.id),
            PipelineNode(prompt_id=downstream.id),
        ],
        edges=[
            PipelineEdge(
                source=upstream.id,
                target=downstream.id,
                kind=_EdgeKind.VARIABLE,
                via="value",
            ),
        ],
        entry_points=[upstream.id],
        exit_points=[downstream.id],
    )
    _seed_pipeline_row(project, pipeline)
    run_id = "test-pipe-sidecar"
    _seed_run_row(project, run_id, pipeline.id, target_kind="pipeline")

    payload = _build_payload(
        target_kind="pipeline",
        target_id=pipeline.id,
        cases=[DatasetCase(inputs={"value": "seed"})],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    records = _read_sidecar_lines(project, run_id)
    assert len(records) == 1
    record = records[0]
    assert record["case_index"] == 0
    # text is the *sink* output (the downstream node's reply).
    assert record["text"] is not None
    # intermediate carries every visited node id keyed to its text.
    intermediate = record["intermediate"]
    assert intermediate is not None
    assert set(intermediate.keys()) == {"p-up", "p-down"}
    # Pipeline mode does not propagate per-case ChatResponses through
    # PipelineRunResult; the forward-looking judge fields are null.
    assert record["cost_usd"] is None
    assert record["usage"] is None


# ---------------------------------------------------------------------------
# Failure-path coverage
# ---------------------------------------------------------------------------


def test_sidecar_written_even_when_every_case_fails(
    project: Settings,
    failing_client_factory: MockLLMClient,
) -> None:
    """All cases erroring inside the runner still produces a sidecar with rows.

    The runner traps per-call exceptions into ``RunOutput.error`` (the
    documented isolation behaviour), so ``invoke_run`` succeeds at the
    run level even though every case failed. The sidecar must still be
    written so the judge can use the error metadata to flag flaky cases.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-all-fail"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[
            DatasetCase(inputs={"value": "a"}),
            DatasetCase(inputs={"value": "b"}),
        ],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    records = _read_sidecar_lines(project, run_id)
    assert len(records) == 2
    # Both rows carry an error string and have no text.
    assert all(r["text"] is None for r in records)
    assert all(r["error"] is not None for r in records)
    assert all("simulated provider outage" in r["error"] for r in records)
    # Failed cases have no cost/usage because no ChatResponse came back.
    assert all(r["cost_usd"] is None for r in records)
    assert all(r["usage"] is None for r in records)


def test_sidecar_empty_when_no_cases(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """``cases=[]`` produces a zero-line sidecar (file present, no rows).

    This is the path the integration smoke test exercises — the writer
    creates the file even when there is nothing to write so the reader
    can distinguish "completed with no cases" (empty file) from
    "dispatch failed before any output" (no file).
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-empty"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(target_id=site.id, cases=[])
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    path = dispatch.outputs_sidecar_path(project, run_id)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""


def test_no_sidecar_written_when_dispatch_fails_at_run_level(
    project: Settings,
) -> None:
    """A run-level failure (missing prompt) writes no sidecar.

    The reader's contract says "missing file → empty list," which is the
    legitimate state for a failed run that never produced per-case
    outputs. We assert the file is absent so we don't accidentally
    create a misleading empty sidecar in this path.
    """
    run_id = "test-no-sidecar"
    _seed_run_row(project, run_id, "no-such-prompt")
    payload = _build_payload(target_id="no-such-prompt", cases=[])

    with pytest.raises(ValueError, match="prompt 'no-such-prompt' not found"):
        dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    path = dispatch.outputs_sidecar_path(project, run_id)
    assert not path.exists()
    # And the reader handles the absence by returning [].
    assert runs_route._load_outputs(project, run_id) == []


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_sidecar_write_is_atomic_via_tmp_then_replace(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash during the final ``os.replace`` leaves no half-written file.

    We monkey-patch :func:`os.replace` (the call the writer uses to
    promote ``outputs.jsonl.tmp`` → ``outputs.jsonl``) to raise after
    the tmp has been fully written. The writer's except block must
    delete the tmp; the final ``outputs.jsonl`` must not exist either,
    because the run-level dispatch will be marked failed and the reader
    will fall back to the empty-list path.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-atomic"
    _seed_run_row(project, run_id, site.id)

    original_replace = os.replace

    def _boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Verify the tmp landed first — that's the whole point of the
        # write-then-rename dance. We don't care about the rename itself
        # because we're about to refuse it.
        assert Path(src).exists(), "tmp file should exist before os.replace"
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _boom)

    payload = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "x"})],
    )
    with pytest.raises(OSError, match="simulated rename failure"):
        dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    # Restore so subsequent assertions / teardown don't trip over the patch.
    monkeypatch.setattr(os, "replace", original_replace)

    target = dispatch.outputs_sidecar_path(project, run_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not target.exists(), "final sidecar must not exist after rename failure"
    assert not tmp.exists(), "writer must clean up tmp on rename failure"


def test_sidecar_overwrite_preserves_previous_on_failure(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed retry must not corrupt a previously-good sidecar.

    Run twice against the same run_id. The first invoke writes a real
    sidecar. The second invoke is configured to fail at ``os.replace``
    — the previous file must survive intact because the writer never
    touches the target on failure.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-atomic-preserve"
    _seed_run_row(project, run_id, site.id)

    # First run: write a real sidecar.
    payload = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "first"})],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)
    target = dispatch.outputs_sidecar_path(project, run_id)
    first_content = target.read_text(encoding="utf-8")
    assert first_content, "first run should have produced a non-empty sidecar"

    # Second run: simulate failure at the rename step.
    def _boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _boom)
    payload_second = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "second"})],
    )
    with pytest.raises(OSError, match="simulated rename failure"):
        dispatch.invoke_run(settings=project, run_id=run_id, payload=payload_second)

    # The good content from the first run is untouched.
    assert target.read_text(encoding="utf-8") == first_content
