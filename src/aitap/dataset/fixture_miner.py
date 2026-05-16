"""Mine the project's ``tests/`` / ``fixtures/`` / ``examples/`` directories
for dict/JSON literals that look like prompt inputs.

Two flavours are recognised:

1. **Inline Python literals** — ``{"role": "user", "content": "..."}`` style,
   pulled out of ``.py`` files via :mod:`ast`.
2. **JSON files** — ``.json`` files whose top-level shape is a dict or a
   list of dicts.

The heuristic for "looks like prompt input" is intentionally loose: at least
one key matching a known prompt-y name (``content``, ``prompt``, ``input``,
``query``, ``messages``, ``system``, ``user``, or any of the
``PromptSite``'s template variable names), with all values resolvable to
JSON-safe Python literals.

This is the cheapest of the four generation modes — it makes a few cases
for free from code the user already wrote.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.dataset.types import Case, case_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aitap.scanner.models import PromptSite


# Directories to scan, in order of likely yield. Limited to the project's
# top-level conventional locations so we don't trawl the entire repo on a
# huge codebase.
_DEFAULT_DIRS: tuple[str, ...] = ("tests", "fixtures", "examples", "test", "test_data")

# Keys whose presence in a dict literal strongly suggests "this is a prompt
# input". The user can also widen the set via ``extra_keys`` — usually by
# passing the prompt site's template variables.
_DEFAULT_KEYS: frozenset[str] = frozenset(
    {
        "content",
        "prompt",
        "input",
        "inputs",
        "query",
        "question",
        "messages",
        "system",
        "user",
        "text",
        "body",
        "topic",
    }
)


def find_candidate_inputs(
    project_root: Path,
    site: PromptSite,
    *,
    search_dirs: Iterable[str] | None = None,
    max_candidates: int = 50,
) -> list[Case]:
    """Return up to *max_candidates* candidate :class:`Case` rows for *site*.

    Each returned case has ``source="fixture"`` and a ``notes`` field that
    points at the file it came from, so the dataset editor can show a "from
    your codebase" provenance.
    """
    interesting_keys: set[str] = set(_DEFAULT_KEYS)
    for msg in site.messages:
        for var in msg.variables:
            interesting_keys.add(var.name)

    dirs_to_walk = list(search_dirs) if search_dirs is not None else list(_DEFAULT_DIRS)
    candidates: list[Case] = []
    seen_ids: set[str] = set()

    for sub in dirs_to_walk:
        root = (project_root / sub).resolve()
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if len(candidates) >= max_candidates:
                break
            if not path.is_file():
                continue
            if path.suffix == ".py":
                literals = _mine_python(path, interesting_keys)
            elif path.suffix == ".json":
                literals = _mine_json(path, interesting_keys)
            else:
                continue
            for inputs, line in literals:
                cid = case_id(site.id, inputs)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                rel = _relative_or_absolute(path, project_root)
                candidates.append(
                    Case(
                        id=cid,
                        prompt_site_id=site.id,
                        inputs=inputs,
                        source="fixture",
                        tags=["fixture"],
                        notes=f"mined from {rel}:{line}",
                    )
                )
                if len(candidates) >= max_candidates:
                    break
    return candidates


# ---------------------------------------------------------------------------
# Python literal extraction
# ---------------------------------------------------------------------------


def _mine_python(path: Path, interesting_keys: set[str]) -> list[tuple[dict[str, object], int]]:
    """Pull dict literals out of *path* that look like prompt inputs.

    Uses :func:`ast.literal_eval` on each Dict node — that already enforces
    "all values must be Python literals" (no function calls, no Names), which
    is exactly the constraint we want: a fixture value that the LLM expander
    or the playground runner can use without further evaluation.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    out: list[tuple[dict[str, object], int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        # Cheap pre-filter: collect string keys only — we don't want to
        # literal_eval dicts whose keys are computed.
        str_keys: list[str] = []
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                str_keys.append(k.value)
        if not str_keys:
            continue
        if not interesting_keys.intersection(str_keys):
            continue
        try:
            value: Any = ast.literal_eval(node)
        except (ValueError, SyntaxError, MemoryError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        coerced = _coerce_inputs(value)
        if coerced is None:
            continue
        out.append((coerced, getattr(node, "lineno", 1)))
    return out


# ---------------------------------------------------------------------------
# JSON file extraction
# ---------------------------------------------------------------------------


def _mine_json(path: Path, interesting_keys: set[str]) -> list[tuple[dict[str, object], int]]:
    """Read *path* as JSON and surface dict-shaped candidates.

    Accepts both a single dict and a list of dicts at the top level — both
    are common shapes for fixture files.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        return []

    candidates: list[dict[str, object]] = []
    if isinstance(raw, dict):
        candidates.append(raw)
    elif isinstance(raw, list):
        candidates.extend(item for item in raw if isinstance(item, dict))
    else:
        return []

    out: list[tuple[dict[str, object], int]] = []
    for c in candidates:
        if not interesting_keys.intersection(c.keys()):
            continue
        coerced = _coerce_inputs(c)
        if coerced is None:
            continue
        # JSON has no per-element line info; surface "1" as a stable
        # sentinel rather than guessing.
        out.append((coerced, 1))
    return out


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _coerce_inputs(raw: dict[Any, Any]) -> dict[str, object] | None:
    """Drop non-string keys and ensure values are JSON-serialisable.

    A candidate is rejected outright if *any* value is non-serialisable —
    fixtures we can't round-trip through the dataset JSONL store aren't
    useful to us. Returns ``None`` on rejection so the caller can continue.
    """
    out: dict[str, object] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            return None
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            return None
        out[k] = v
    return out or None


def _relative_or_absolute(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = ["find_candidate_inputs"]
