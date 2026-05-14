"""End-to-end orchestrator tests + dedup + WCC pipeline construction."""

from __future__ import annotations

from pathlib import Path

from aitap.scanner.dataflow import (
    build_pipelines_from_edges,
    dedupe_edges,
)
from aitap.scanner.engine import scan_project
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    PipelineEdge,
    PromptSite,
    Provider,
    Role,
)


def _site(prompt_id: str, line: int, name: str = "x") -> PromptSite:
    return PromptSite(
        id=prompt_id,
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=line, line_end=line),
        messages=[Message(role=Role.USER, template_text="hi")],
        parameters=CallParameters(),
        confidence=Confidence.HIGH,
    )


def _edge(
    src: str, tgt: str, kind: EdgeKind = EdgeKind.VARIABLE, conf: Confidence = Confidence.HIGH
) -> PipelineEdge:
    return PipelineEdge(source=src, target=tgt, kind=kind, confidence=conf)


# --------------------------------------------------------------------------- #
# dedupe_edges                                                                #
# --------------------------------------------------------------------------- #


def test_dedupe_edges_keeps_singletons() -> None:
    edges = [_edge("a", "b"), _edge("b", "c")]
    assert len(dedupe_edges(edges)) == 2


def test_dedupe_edges_collapses_duplicate_kind() -> None:
    edges = [_edge("a", "b", conf=Confidence.LOW), _edge("a", "b", conf=Confidence.HIGH)]
    deduped = dedupe_edges(edges)
    assert len(deduped) == 1
    assert deduped[0].confidence == Confidence.HIGH


def test_dedupe_edges_keeps_distinct_kinds() -> None:
    edges = [
        _edge("a", "b", kind=EdgeKind.VARIABLE),
        _edge("a", "b", kind=EdgeKind.LANGCHAIN_PIPE),
    ]
    deduped = dedupe_edges(edges)
    assert len(deduped) == 2


# --------------------------------------------------------------------------- #
# build_pipelines_from_edges                                                  #
# --------------------------------------------------------------------------- #


def test_build_pipelines_returns_empty_when_no_edges() -> None:
    sites = [_site("a", 1), _site("b", 2)]
    assert build_pipelines_from_edges([], sites) == []


def test_build_pipelines_one_chain() -> None:
    sites = [_site("a", 1, "outline"), _site("b", 5, "polish"), _site("c", 9, "critic")]
    edges = [_edge("a", "b"), _edge("b", "c")]
    pipelines = build_pipelines_from_edges(edges, sites)

    assert len(pipelines) == 1
    pipe = pipelines[0]
    assert {n.prompt_id for n in pipe.nodes} == {"a", "b", "c"}
    assert pipe.entry_points == ["a"]
    assert pipe.exit_points == ["c"]


def test_build_pipelines_disconnected_components() -> None:
    sites = [_site("a", 1), _site("b", 2), _site("c", 3), _site("d", 4)]
    edges = [_edge("a", "b"), _edge("c", "d")]
    pipelines = build_pipelines_from_edges(edges, sites)
    assert len(pipelines) == 2


def test_build_pipelines_isolated_sites_excluded() -> None:
    """Sites with no edges shouldn't show up in any Pipeline."""
    sites = [_site("a", 1), _site("b", 2), _site("loner", 3)]
    edges = [_edge("a", "b")]
    pipelines = build_pipelines_from_edges(edges, sites)
    assert len(pipelines) == 1
    assert "loner" not in {n.prompt_id for n in pipelines[0].nodes}


def test_build_pipelines_id_is_stable() -> None:
    """Same input → same Pipeline.id (used by the store as PK)."""
    sites = [_site("a", 1), _site("b", 2)]
    edges = [_edge("a", "b")]
    p1 = build_pipelines_from_edges(edges, sites)
    p2 = build_pipelines_from_edges(edges, sites)
    assert p1[0].id == p2[0].id


# --------------------------------------------------------------------------- #
# detect_pipelines (file-driven)                                              #
# --------------------------------------------------------------------------- #


def test_detect_pipelines_on_var_chain_fixture() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "var_chain"
    result = scan_project(fixture)

    assert len(result.prompts) == 2, f"expected 2 prompts, got {len(result.prompts)}"
    assert len(result.pipelines) == 1
    pipe = result.pipelines[0]
    assert len(pipe.edges) >= 1
    assert any(e.kind == EdgeKind.VARIABLE for e in pipe.edges)
    assert {n.prompt_id for n in pipe.nodes} == {s.id for s in result.prompts}


def test_detect_pipelines_on_langchain_rag_fixture() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "langchain_rag"
    result = scan_project(fixture)

    assert len(result.prompts) == 3, f"expected 3 prompts, got {len(result.prompts)}"
    # Three prompts connected via variables (rewritten → answer → critique)
    assert len(result.pipelines) >= 1
    pipe = result.pipelines[0]
    assert len(pipe.edges) >= 2


def test_detect_pipelines_handles_unparseable(tmp_path: Path) -> None:
    """A file that fails to parse should be skipped silently."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n")
    result = scan_project(tmp_path)
    # No prompts found in the broken file → no pipelines either; never raises.
    assert result.pipelines == []


def test_detect_pipelines_returns_empty_for_isolated_sites(tmp_path: Path) -> None:
    """Two unrelated LLM calls in the same file but no shared variable → no pipeline."""
    src = tmp_path / "lonely.py"
    src.write_text(
        "from openai import OpenAI\n"
        "c = OpenAI()\n"
        "def a():\n"
        "    return c.chat.completions.create(model='m', messages=[{'role':'user','content':'hi'}])\n"
        "def b():\n"
        "    return c.chat.completions.create(model='m', messages=[{'role':'user','content':'bye'}])\n"
    )
    result = scan_project(tmp_path)
    assert len(result.prompts) >= 2
    # No data flow connecting them → no pipelines.
    assert result.pipelines == []
