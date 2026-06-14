"""Pipeline detection — Wave 2 (方案 A scope).

Public surface:

- :func:`detect_pipelines` — orchestrator the scanner calls once it has
  the prompt sites for a project. Runs every detector against each Python
  file's AST, dedupes the resulting edges, and groups them into Pipelines
  via weakly-connected-component union-find.

Out of scope for this wave (kept for v0.2 L2):

- Cross-file / cross-module data flow
- Cross-class state mutations (``self.x = ...``)
- Agent-loop / control-flow-driven chains
- Aliasing through container unpacking, attribute assignment

Detectors are intentionally independent — each one only emits edges it
can stand behind on its own evidence. The orchestrator's dedup keeps
high-confidence edges when multiple detectors propose the same link.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from .base import (
    DataflowDetector,
    build_pipelines_from_edges,
    dedupe_edges,
)
from .cross_file_orchestration import CrossFileOrchestration
from .intra_class_method_chain import IntraClassMethodChain
from .intra_file_chain import IntraFileChain
from .langchain_pipe import LangChainPipe
from .llamaindex_engine import LlamaIndexEngine
from .variable_tracker import VariableTracker

if TYPE_CHECKING:
    from aitap.scanner.models import Pipeline, PipelineEdge, PromptSite

__all__ = [
    "CrossFileOrchestration",
    "DataflowDetector",
    "IntraClassMethodChain",
    "IntraFileChain",
    "LangChainPipe",
    "LlamaIndexEngine",
    "VariableTracker",
    "build_pipelines_from_edges",
    "dedupe_edges",
    "default_cross_file_detectors",
    "default_detectors",
    "detect_pipelines",
]


def default_detectors() -> list[DataflowDetector]:
    """The MVP intra-file detector roster. Order doesn't affect correctness
    — the orchestrator dedupes — but it does affect which detector "wins"
    when multiple propose the same edge with equal confidence; first wins.

    :class:`IntraClassMethodChain` (B1) sits between the free-function
    detectors and the LlamaIndex engine rule: it covers the
    multi-turn-engine shape (``self.<method>(...)`` orchestration of
    LLM-bearing methods on the same class) that the others miss by
    construction.
    """
    return [
        VariableTracker(),
        LangChainPipe(),
        IntraFileChain(),
        IntraClassMethodChain(),
        LlamaIndexEngine(),
    ]


def default_cross_file_detectors() -> list[CrossFileOrchestration]:
    """Detectors that need the full project view (every file + every site),
    not just one file at a time. Currently the cross-file orchestration
    rule (PR #51) is the only entry.
    """
    return [CrossFileOrchestration()]


def detect_pipelines(
    files: list[Path],
    project_root: Path,
    sites: list[PromptSite],
    *,
    detectors: list[DataflowDetector] | None = None,
    cross_file_detectors: list[CrossFileOrchestration] | None = None,
) -> list[Pipeline]:
    """Detect data-flow Pipelines across *files* given the already-extracted *sites*.

    Two passes:

    1. **Intra-file**: each detector in *detectors* runs against one file's
       AST + that file's prompt sites. Files with fewer than two sites are
       skipped because intra-file dataflow needs something to chain.
    2. **Cross-file**: each detector in *cross_file_detectors* gets every
       file path + every prompt site in the project. The cross-file
       orchestration rule (PR #51) lives here — an orchestrator file with
       zero own LLM sites sequences ``self.<attr>.<method>(...)`` calls
       where each ``<attr>`` resolves through ``__init__`` to a class
       defined in another LLM-bearing file.

    Each Python file is parsed once and shared across all intra-file
    detectors so the cost stays linear regardless of detector count. Files
    that fail to parse are skipped silently — the scanner already recorded
    a ScanWarning for them earlier in the pipeline.
    """
    detectors = detectors or default_detectors()
    cross_file_detectors = (
        cross_file_detectors if cross_file_detectors is not None else default_cross_file_detectors()
    )
    sites_by_file = _group_sites_by_file(sites)

    all_edges: list[PipelineEdge] = []
    for file_path in files:
        rel = _relative(file_path, project_root)
        sites_in_file = sites_by_file.get(rel, [])
        if len(sites_in_file) < 2:
            # Without at least two sites in a file there's nothing for any
            # of these intra-file detectors to chain.
            continue

        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, SyntaxError):
            continue

        for detector in detectors:
            try:
                edges = detector.detect(tree, sites_in_file, file_path)
            except Exception:
                # A buggy detector mustn't blow up the whole scan; just
                # skip its contribution for this file.
                continue
            all_edges.extend(edges)

    # Cross-file pass — see ``CrossFileOrchestration`` docstring for shape.
    for cross_detector in cross_file_detectors:
        try:
            cross_edges = cross_detector.detect(files, project_root, sites)
        except Exception:
            continue
        all_edges.extend(cross_edges)

    return build_pipelines_from_edges(dedupe_edges(all_edges), sites)


def _group_sites_by_file(sites: list[PromptSite]) -> dict[str, list[PromptSite]]:
    grouped: dict[str, list[PromptSite]] = defaultdict(list)
    for site in sites:
        grouped[site.location.file].append(site)
    return grouped


def _relative(file_path: Path, project_root: Path) -> str:
    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()
