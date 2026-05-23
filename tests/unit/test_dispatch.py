"""Unit tests for :mod:`aitap.playground.dispatch`.

The adapter is the bridge between the FastAPI route layer and the pure
playground runner. We exercise it directly here (no ``TestClient``) so a
regression in either persistence or runner dispatch surfaces in
isolation — separately from the integration tests in
``tests/integration/test_api_runs.py`` which cover the HTTP shape.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

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
from aitap.server.routes import DatasetCase, RunCreate
from aitap.store import db as store_db
from aitap.store import runs as runs_dao


@pytest.fixture()
def project(tmp_path: Path) -> Settings:
    """A throwaway Settings rooted at ``tmp_path`` with ``.aitap`` ready."""
    aitap_dir = tmp_path / ".aitap"
    for child in ("prompts", "pipelines", "datasets", "runs"):
        (aitap_dir / child).mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


@pytest.fixture()
def mock_client_factory() -> Iterator[MockLLMClient]:
    """Install a MockLLMClient as the dispatch adapter's factory.

    The yielded instance is the *first* one constructed — subsequent
    construction inside ``invoke_run`` returns the same one so tests can
    inspect its ``calls`` list after the fact. ``set_client_factory(None)``
    restores the production wiring on teardown so the suite stays clean.
    """
    mock = MockLLMClient(model="mock-model", scripted=["mocked reply"])
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


def _seed_run_row(project: Settings, run_id: str, target_id: str) -> None:
    conn = _open_conn(project)
    try:
        runs_dao.insert_run(
            conn,
            run_id=run_id,
            target_kind="prompt",
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


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_invoke_run_marks_run_done_with_cost(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """Running a one-case prompt flips status to ``done`` and stamps cost."""
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-run-1"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(
        target_id=site.id,
        cases=[DatasetCase(inputs={"value": "hello"})],
    )

    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    conn = _open_conn(project)
    try:
        row = runs_dao.read_run(conn, run_id)
    finally:
        conn.close()

    assert row is not None
    assert row["status"] == "done"
    # MockLLMClient hardcodes cost_usd=0.0001 per call; one case == one call.
    assert float(row["cost_usd"]) > 0.0
    assert row["finished_at"] is not None
    # The mock was actually invoked (sanity-check the test fixture wiring).
    assert len(mock_client_factory.calls) == 1


def test_invoke_run_zero_cases_still_marks_done(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """``cases=[]`` is a no-op chat-wise but must still flip status to ``done``.

    This is the path the integration smoke test exercises — it asserts the
    state machine works even when the runner has nothing to do.
    """
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-run-empty"
    _seed_run_row(project, run_id, site.id)

    payload = _build_payload(target_id=site.id, cases=[])
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    conn = _open_conn(project)
    try:
        row = runs_dao.read_run(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert float(row["cost_usd"]) == 0.0
    assert len(mock_client_factory.calls) == 0


def test_invoke_run_loads_cases_from_dataset_sidecar(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """When ``payload.cases`` is empty but ``dataset_id`` is set, read the JSONL."""
    site = _prompt_site()
    _seed_prompt_row(project, site)
    run_id = "test-run-ds"
    _seed_run_row(project, run_id, site.id)

    # Hand-write the sidecar so we don't depend on the dataset module.
    dataset_path = project.datasets_dir / "my-cases.cases.jsonl"
    dataset_path.write_text(
        "\n".join(
            [
                json.dumps({"value": "first"}),
                json.dumps({"value": "second"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = RunCreate(
        target_kind="prompt",
        target_id=site.id,
        target_version=1,
        cases=[],
        dataset_id="my-cases",
        provider=Provider.ANTHROPIC,
        model="mock-model",
        parameters=CallParameters(temperature=0.0),
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    conn = _open_conn(project)
    try:
        row = runs_dao.read_run(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    # Two cases in the sidecar => two MockLLMClient.chat invocations.
    assert len(mock_client_factory.calls) == 2


# ---------------------------------------------------------------------------
# Pipeline path
# ---------------------------------------------------------------------------


def test_invoke_run_pipeline_end_to_end(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """A two-node pipeline runs end-to-end and persists a terminal status."""
    upstream = _prompt_site("p-up")
    downstream = _prompt_site("p-down")
    _seed_prompt_row(project, upstream)
    _seed_prompt_row(project, downstream)
    pipeline = Pipeline(
        id="pipe-1",
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
    run_id = "test-run-pipe"

    conn = _open_conn(project)
    try:
        runs_dao.insert_run(
            conn,
            run_id=run_id,
            target_kind="pipeline",
            target_id=pipeline.id,
            target_version=1,
            provider="anthropic",
            model="mock-model",
            parameters_json="{}",
        )
    finally:
        conn.close()

    payload = _build_payload(
        target_kind="pipeline",
        target_id=pipeline.id,
        cases=[DatasetCase(inputs={"value": "seed"})],
    )
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    conn = _open_conn(project)
    try:
        row = runs_dao.read_run(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    # Two nodes * one case each => two MockLLMClient.chat invocations.
    assert len(mock_client_factory.calls) == 2


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pipeline mode routing (A·D4: dispatch honours pipeline_mode)
# ---------------------------------------------------------------------------


def _seed_three_node_pipeline(project: Settings) -> Pipeline:
    """A → B → C linear pipeline with all three node sites seeded.

    Linear so segment/end_to_end topological order is deterministic and we
    can assert call counts against a known node set.
    """
    a = _prompt_site("p-a")
    b = _prompt_site("p-b")
    c = _prompt_site("p-c")
    for site in (a, b, c):
        _seed_prompt_row(project, site)
    pipeline = Pipeline(
        id="pipe-3",
        name="three_step",
        nodes=[
            PipelineNode(prompt_id=a.id),
            PipelineNode(prompt_id=b.id),
            PipelineNode(prompt_id=c.id),
        ],
        edges=[
            PipelineEdge(source=a.id, target=b.id, kind=_EdgeKind.VARIABLE, via="value"),
            PipelineEdge(source=b.id, target=c.id, kind=_EdgeKind.VARIABLE, via="value"),
        ],
        entry_points=[a.id],
        exit_points=[c.id],
    )
    _seed_pipeline_row(project, pipeline)
    return pipeline


def _seed_pipeline_run_row(project: Settings, run_id: str, pipeline_id: str) -> None:
    conn = _open_conn(project)
    try:
        runs_dao.insert_run(
            conn,
            run_id=run_id,
            target_kind="pipeline",
            target_id=pipeline_id,
            target_version=1,
            provider="anthropic",
            model="mock-model",
            parameters_json="{}",
        )
    finally:
        conn.close()


def _pipeline_payload(
    pipeline_id: str,
    *,
    mode: str | None = None,
    node_id: str | None = None,
    segment: list[str] | None = None,
) -> RunCreate:
    return RunCreate(
        target_kind="pipeline",
        target_id=pipeline_id,
        target_version=1,
        cases=[DatasetCase(inputs={"value": "seed"})],
        provider=Provider.ANTHROPIC,
        model="mock-model",
        parameters=CallParameters(temperature=0.0),
        pipeline_mode=mode,  # type: ignore[arg-type]
        pipeline_node_id=node_id,
        pipeline_segment=segment,
    )


def test_dispatch_node_mode_routes_to_runner(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pipeline_mode='node'`` forwards mode + node_id to ``run_pipeline``."""
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-node-spy"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    captured: dict[str, object] = {}
    real_run_pipeline = dispatch.run_pipeline

    async def _spy(pipeline_arg, mode, **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        captured["node_id"] = kwargs.get("node_id")
        captured["segment"] = kwargs.get("segment")
        return await real_run_pipeline(pipeline_arg, mode, **kwargs)

    monkeypatch.setattr(dispatch, "run_pipeline", _spy)

    payload = _pipeline_payload(pipeline.id, mode="node", node_id="p-b")
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    assert captured["mode"] == "node"
    assert captured["node_id"] == "p-b"
    assert captured["segment"] is None
    # node mode runs exactly one node for the single case.
    assert len(mock_client_factory.calls) == 1


def test_dispatch_segment_mode_routes_to_runner(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pipeline_mode='segment'`` forwards mode + segment to ``run_pipeline``."""
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-seg-spy"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    captured: dict[str, object] = {}
    real_run_pipeline = dispatch.run_pipeline

    async def _spy(pipeline_arg, mode, **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        captured["node_id"] = kwargs.get("node_id")
        captured["segment"] = kwargs.get("segment")
        return await real_run_pipeline(pipeline_arg, mode, **kwargs)

    monkeypatch.setattr(dispatch, "run_pipeline", _spy)

    payload = _pipeline_payload(pipeline.id, mode="segment", segment=["p-a", "p-b"])
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    assert captured["mode"] == "segment"
    assert captured["segment"] == ["p-a", "p-b"]
    assert captured["node_id"] is None
    # segment {a, b} runs two nodes for the single case (c is excluded).
    assert len(mock_client_factory.calls) == 2


def test_dispatch_end_to_end_mode_routes_to_runner(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``pipeline_mode='end_to_end'`` walks the whole DAG."""
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-e2e-spy"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    captured: dict[str, object] = {}
    real_run_pipeline = dispatch.run_pipeline

    async def _spy(pipeline_arg, mode, **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        return await real_run_pipeline(pipeline_arg, mode, **kwargs)

    monkeypatch.setattr(dispatch, "run_pipeline", _spy)

    payload = _pipeline_payload(pipeline.id, mode="end_to_end")
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    assert captured["mode"] == "end_to_end"
    # All three nodes run for the single case.
    assert len(mock_client_factory.calls) == 3


def test_dispatch_mode_none_is_end_to_end_regression(
    project: Settings,
    mock_client_factory: MockLLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``pipeline_mode`` field behaves exactly like before (end_to_end).

    Regression guard for the additive contract change: an old client that
    never sends a mode must still walk the whole DAG.
    """
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-none-spy"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    captured: dict[str, object] = {}
    real_run_pipeline = dispatch.run_pipeline

    async def _spy(pipeline_arg, mode, **kwargs):  # type: ignore[no-untyped-def]
        captured["mode"] = mode
        captured["node_id"] = kwargs.get("node_id")
        captured["segment"] = kwargs.get("segment")
        return await real_run_pipeline(pipeline_arg, mode, **kwargs)

    monkeypatch.setattr(dispatch, "run_pipeline", _spy)

    payload = _pipeline_payload(pipeline.id)  # no mode/node_id/segment
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    assert captured["mode"] == "end_to_end"
    assert captured["node_id"] is None
    assert captured["segment"] is None
    # All three nodes run — identical to the pre-change behaviour.
    assert len(mock_client_factory.calls) == 3


def test_dispatch_segment_mode_produces_intermediate(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """A real segment run records per-node intermediates in the sidecar."""
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-seg-real"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    payload = _pipeline_payload(pipeline.id, mode="segment", segment=["p-a", "p-b"])
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    sidecar = dispatch.outputs_sidecar_path(project, run_id)
    records = [
        json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(records) == 1
    intermediate = records[0]["intermediate"]
    # Only the two segment nodes appear; c is out of scope.
    assert set(intermediate) == {"p-a", "p-b"}


def test_dispatch_node_mode_runs_only_one_node(
    project: Settings,
    mock_client_factory: MockLLMClient,
) -> None:
    """Node mode runs a single node and emits no intermediate map.

    This also exercises the latent ``node`` no-op bug fix: before A·D4 the
    dispatch always ran end_to_end regardless of the requested node.
    """
    pipeline = _seed_three_node_pipeline(project)
    run_id = "run-node-real"
    _seed_pipeline_run_row(project, run_id, pipeline.id)

    payload = _pipeline_payload(pipeline.id, mode="node", node_id="p-c")
    dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    # Exactly one chat call for the single targeted node + single case.
    assert len(mock_client_factory.calls) == 1
    sidecar = dispatch.outputs_sidecar_path(project, run_id)
    records = [
        json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(records) == 1
    # Node mode delegates to run_prompt, which produces no intermediate map.
    assert records[0]["intermediate"] is None


def test_invoke_run_unknown_prompt_marks_failed(project: Settings) -> None:
    """A missing target prompt flips the run to ``failed`` and re-raises."""
    run_id = "test-run-missing"
    _seed_run_row(project, run_id, "no-such-prompt")
    payload = _build_payload(target_id="no-such-prompt", cases=[])

    with pytest.raises(ValueError, match="prompt 'no-such-prompt' not found"):
        dispatch.invoke_run(settings=project, run_id=run_id, payload=payload)

    conn = _open_conn(project)
    try:
        row = runs_dao.read_run(conn, run_id)
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["finished_at"] is not None


def test_set_client_factory_none_restores_default() -> None:
    """Calling ``set_client_factory(None)`` reverts to the production factory."""
    sentinel = object()
    dispatch.set_client_factory(lambda provider, model: sentinel)  # type: ignore[arg-type,return-value]
    assert dispatch._client_factory("anthropic", "claude-sonnet-4-6") is sentinel
    dispatch.set_client_factory(None)
    # The default factory is the module-level function — not the sentinel.
    assert dispatch._client_factory is dispatch._default_client_factory


def test_outputs_sidecar_path_layout(tmp_path: Path) -> None:
    """The sidecar layout is the contract M4 will write outputs into."""
    settings = Settings(project_root=tmp_path)
    path = dispatch.outputs_sidecar_path(settings, "run-123")
    assert path.name == "outputs.jsonl"
    assert path.parent.name == "run-123"
    assert path.parent.parent == settings.runs_dir
