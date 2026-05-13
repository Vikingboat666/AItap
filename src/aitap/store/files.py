"""YAML/JSONL artifacts under ``.aitap/`` that are meant to be git-tracked.

Layout (mirrors the user-facing directory structure documented in the plan):

    .aitap/
    ├── prompts/<name>.prompt.yaml      # one PromptSite per file
    ├── pipelines/<name>.pipeline.yaml  # one Pipeline per file
    └── datasets/<name>.cases.jsonl     # appended on iteration (M4)

Why YAML for prompts/pipelines and JSONL for datasets:

- Prompts/pipelines are *small*, *reviewable*, and meant for PR diff
  inspection — YAML's block style with stable key ordering wins.
- Datasets are *append-mostly* and may grow; line-delimited JSON is
  trivially appendable + diff-friendly per-row.

Determinism is load-bearing: ``write_*`` functions must produce byte-stable
output for the same input so reruns don't churn git history. We sort
nothing automatically (PromptSite/Pipeline already have a deterministic
field order from the pydantic model), but we do pin
``yaml.safe_dump(default_flow_style=False, sort_keys=False)`` to match.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aitap.scanner.models import Pipeline, PromptSite

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Sanitize ``name`` for use as a filename component.

    Names come from PromptSite.name / Pipeline.name which are derived from
    user code (function names, variable names) — usually safe but not
    guaranteed. We collapse anything that isn't ASCII-alnum/dot/dash/underscore
    into a single hyphen so we never write outside the target directory.
    """
    cleaned = _SAFE_NAME_RE.sub("-", name).strip("-._")
    return cleaned or "unnamed"


def _dump_yaml(data: dict[str, object]) -> str:
    """Single source of truth for YAML formatting.

    ``sort_keys=False`` preserves the pydantic field order which matches the
    contract definition — much easier to read than alphabetised soup.
    """
    return yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


# --------------------------------------------------------------------------- #
# Prompts                                                                     #
# --------------------------------------------------------------------------- #


# Length of the id suffix appended to filenames. PromptSite.id is a content
# hash; the first 8 chars give 16M-collision-resistant uniqueness which is
# more than enough for any one project, and they keep filenames readable
# (workflow.84af73be.prompt.yaml).
_ID_SUFFIX_LEN = 8


def prompt_path(prompts_dir: Path, site: PromptSite) -> Path:
    """Return the YAML path for *site*.

    The filename embeds a short prefix of ``site.id`` so two PromptSites
    that derive the same human-friendly name (a common case: multiple LLM
    calls inside one wrapper function) don't overwrite each other on disk.
    Without this disambiguation the SQLite store would carry both rows
    while the YAML mirror silently lost all but the last write.
    """
    return prompts_dir / f"{_safe_filename(site.name)}.{site.id[:_ID_SUFFIX_LEN]}.prompt.yaml"


def write_prompt(prompts_dir: Path, site: PromptSite) -> Path:
    """Write a single PromptSite to ``<prompts_dir>/<name>.prompt.yaml``.

    Returns the path written. ``prompts_dir`` must already exist (created
    by ``aitap init``); we don't auto-mkdir to keep the persistence layer
    aligned with the contract that ``init`` is the only directory-creator.
    """
    path = prompt_path(prompts_dir, site)
    path.write_text(_dump_yaml(site.model_dump(mode="json")), encoding="utf-8")
    return path


def read_prompt(path: Path) -> PromptSite:
    """Round-trip a prompt YAML file back into a PromptSite."""
    from aitap.scanner.models import PromptSite as _PromptSite

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _PromptSite.model_validate(data)


def list_prompts(prompts_dir: Path) -> list[Path]:
    if not prompts_dir.exists():
        return []
    return sorted(prompts_dir.glob("*.prompt.yaml"))


# --------------------------------------------------------------------------- #
# Pipelines                                                                   #
# --------------------------------------------------------------------------- #


def pipeline_path(pipelines_dir: Path, pipeline: Pipeline) -> Path:
    """Return the YAML path for *pipeline*.

    Same id-suffix discipline as :func:`prompt_path` — pipeline names are
    derived from anchor prompt names and can collide for similar reasons.
    """
    return (
        pipelines_dir
        / f"{_safe_filename(pipeline.name)}.{pipeline.id[:_ID_SUFFIX_LEN]}.pipeline.yaml"
    )


def write_pipeline(pipelines_dir: Path, pipeline: Pipeline) -> Path:
    path = pipeline_path(pipelines_dir, pipeline)
    path.write_text(_dump_yaml(pipeline.model_dump(mode="json")), encoding="utf-8")
    return path


def read_pipeline(path: Path) -> Pipeline:
    from aitap.scanner.models import Pipeline as _Pipeline

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _Pipeline.model_validate(data)


def list_pipelines(pipelines_dir: Path) -> list[Path]:
    if not pipelines_dir.exists():
        return []
    return sorted(pipelines_dir.glob("*.pipeline.yaml"))


# --------------------------------------------------------------------------- #
# Datasets (append-only JSONL)                                                #
# --------------------------------------------------------------------------- #


def dataset_path(datasets_dir: Path, name: str) -> Path:
    return datasets_dir / f"{_safe_filename(name)}.cases.jsonl"


def append_cases(datasets_dir: Path, name: str, cases: Iterable[dict[str, object]]) -> Path:
    """Append one JSON object per line to the dataset file.

    Each ``case`` is serialised with sorted top-level keys so a re-emission of
    the same dict produces byte-stable output (helps when an iteration step
    re-emits a known case).
    """
    path = dataset_path(datasets_dir, name)
    with path.open("a", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    return path


def read_cases(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out
