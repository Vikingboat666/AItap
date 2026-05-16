"""Internal data structures for the dataset generators.

``Case`` and ``InputShape`` are *dataset-internal* types — not part of the
cross-worktree scanner contract (see ``CONTRACTS.md``). They live here so
``aitap.dataset.*`` modules can talk about cases in a typed way while
``store/files.append_cases`` keeps its dict-of-anything I/O surface.

Wire shape on disk (``.aitap/datasets/<name>.cases.jsonl``)::

    {"id": "<sha1[:12]>", "inputs": {...}, "tags": ["seed", "boundary"],
     "expected": null, "source": "seed", "prompt_site_id": "<site.id>",
     "notes": null}

The hash-derived id keeps the same logical case stable across reruns so
re-emitting it is a no-op in git diff (the JSONL store already sorts keys
for byte stability).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CaseSource = Literal["seed", "expand", "fixture", "context"]
"""Provenance of a Case — surfaced in the dataset editor so reviewers can
filter out auto-generated rows when curating a gold set."""


class Case(BaseModel):
    """A single test case for a PromptSite.

    ``inputs`` is intentionally ``dict[str, object]`` — different prompts
    have wildly different input shapes (some take a single ``query``
    string, others take 4 named slots). ``expected`` is optional because
    most cases start without an oracle; the iterate loop (M4) fills it in
    via critique-and-revise.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """Stable 12-char hash of (prompt_site_id, inputs). Re-emitting the same
    logical case produces the same row."""

    prompt_site_id: str
    """``PromptSite.id`` this case belongs to — keeps datasets joinable back
    to the scanner output."""

    inputs: dict[str, object] = Field(default_factory=dict)
    """Named slots that will fill the prompt's template variables, plus any
    extra context the prompt needs."""

    expected: str | None = None
    """Optional expected output / gold answer. Left ``None`` for cases that
    only exercise behaviour, not correctness."""

    tags: list[str] = Field(default_factory=list)
    """Free-form labels: ``boundary``, ``adversarial``, ``noise``, ``happy``,
    etc. The LLM expander emits these so the dataset editor can group cases
    visually."""

    source: CaseSource = "seed"
    """How this case got into the dataset. Seeds came from the user;
    ``expand``/``fixture``/``context`` are auto-generated."""

    notes: str | None = None
    """Optional human-readable note (e.g. "covers empty-body regression").
    The expander uses it to record *why* a variant was generated."""


class InputShape(BaseModel):
    """A best-effort description of what the prompt expects as input.

    Built by :func:`aitap.dataset.code_context.infer_input_shape` from the
    surrounding function signature; consumed by
    :func:`aitap.dataset.llm_expander.expand` as grounding so the LLM
    doesn't invent inputs that the prompt couldn't possibly use.

    ``fields`` maps a slot name to a free-form type description — usually
    a Python type spelling pulled from the function annotation
    (``"str"``, ``"list[str]"``, ``"dict"``), but the LLM is allowed to
    treat it as a hint, not a hard schema.
    """

    model_config = ConfigDict(frozen=True)

    fields: dict[str, str] = Field(default_factory=dict)
    """slot name -> short type description, e.g. ``{"body": "str"}``."""

    function_name: str | None = None
    """Enclosing function (or class) name, when we could resolve one."""

    docstring: str | None = None
    """First paragraph of the enclosing function's docstring, when present."""

    def is_empty(self) -> bool:
        """True when we couldn't infer anything useful."""
        return not self.fields and not self.function_name and not self.docstring


def case_id(prompt_site_id: str, inputs: dict[str, object]) -> str:
    """Compute the stable id for a (site, inputs) pair.

    Uses ``json.dumps(sort_keys=True)`` for the inputs so logically equal
    dicts in different key order land on the same id. The 12-char SHA-1
    prefix matches the convention used by :func:`PromptSite.id`.
    """
    # Defensive: caller may pass non-JSON-serialisable objects (sets, etc).
    # Fall back to ``default=str`` so we never crash on the id path — the
    # cost is occasional cross-version drift on weird inputs, which is
    # acceptable for an id used for de-duplication.
    payload = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha1(f"{prompt_site_id}\0{payload}".encode())
    return digest.hexdigest()[:12]


__all__ = ["Case", "CaseSource", "InputShape", "case_id"]
