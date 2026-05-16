"""Read/write user-provided seed cases for a PromptSite.

The on-disk store is the project-tracked ``.aitap/datasets/<name>.cases.jsonl``
managed by ``aitap.store.files``. This module is a thin typed layer on top:

- :func:`load_seeds` reads the file and rehydrates ``Case`` objects.
- :func:`save_seeds` normalises and appends new ``Case`` rows, deduplicating
  against any already-on-disk by their hash id.

We never *rewrite* the JSONL file — only append — so the store's byte-stable
guarantees hold and git diffs stay sane. Deduplication therefore happens
in-memory inside :func:`save_seeds` before writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.dataset.types import Case, case_id
from aitap.store.files import append_cases, dataset_path, read_cases

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def _normalize_case(raw: Case | Mapping[str, Any], *, prompt_site_id: str) -> Case:
    """Coerce a dict-ish seed (what a UI would POST) into a :class:`Case`.

    Two responsibilities:

    * Fill in ``prompt_site_id`` if missing — most callers know the site
      from context and don't bother including it in the literal.
    * Compute/repair the case ``id`` from its inputs so two equal inputs
      always resolve to the same row.
    """
    if isinstance(raw, Case):
        # Trust the caller's id if present; otherwise recompute. Either way
        # rebind ``prompt_site_id`` so a copy-paste between sites doesn't
        # mis-attribute the row.
        site_id = raw.prompt_site_id or prompt_site_id
        cid = raw.id or case_id(site_id, raw.inputs)
        return raw.model_copy(update={"id": cid, "prompt_site_id": site_id})

    # Mapping path — accept partial/loose dicts.
    data: dict[str, Any] = dict(raw)
    site_id = str(data.get("prompt_site_id") or prompt_site_id)
    inputs_raw: Any = data.get("inputs", {})
    if not isinstance(inputs_raw, dict):
        raise TypeError(f"seed case 'inputs' must be a dict, got {type(inputs_raw).__name__}")
    inputs: dict[str, object] = dict(inputs_raw)
    data["inputs"] = inputs
    data["prompt_site_id"] = site_id
    data.setdefault("source", "seed")
    if not data.get("id"):
        data["id"] = case_id(site_id, inputs)
    return Case.model_validate(data)


def normalize_seeds(
    seeds: Iterable[Case | Mapping[str, Any]], *, prompt_site_id: str
) -> list[Case]:
    """Normalise an arbitrary iterable of seed-ish things to ``list[Case]``.

    Exposed for callers (e.g. the LLM expander) that want the same coercion
    rules without touching disk.
    """
    return [_normalize_case(s, prompt_site_id=prompt_site_id) for s in seeds]


def load_seeds(datasets_dir: Path, name: str) -> list[Case]:
    """Read all cases currently on disk for *name*.

    Returns an empty list if the file doesn't exist yet — first-run is the
    common case and shouldn't require try/except at the callsite.
    """
    path = dataset_path(datasets_dir, name)
    rows = read_cases(path)
    out: list[Case] = []
    for row in rows:
        try:
            out.append(Case.model_validate(row))
        except Exception:  # defensive: skip malformed rows; see comment below
            # Older datasets may have rows that predate the Case schema; we
            # tolerate them by skipping (the iterate loop will re-emit them
            # when it next runs). Re-raising here would lock users out of
            # their own dataset over one bad line.
            continue
    return out


def save_seeds(
    datasets_dir: Path,
    name: str,
    seeds: Iterable[Case | Mapping[str, Any]],
    *,
    prompt_site_id: str,
) -> list[Case]:
    """Normalise *seeds*, drop rows already on disk, append the rest.

    Returns the list of cases actually appended (after deduplication) so the
    caller can tell whether anything new landed.

    Why dedupe by ``Case.id`` not by full-row equality: the id already
    incorporates ``prompt_site_id + inputs`` (see :func:`case_id`), which
    is the only thing that has to be unique. Tags/notes can legitimately
    differ between successive saves and we want the latest version to win
    — but since we *append* (never overwrite) we just drop the newer
    duplicate; the consumer reads all rows and the JSONL store will
    naturally pick the last one if it dedupes downstream.
    """
    normalised = normalize_seeds(seeds, prompt_site_id=prompt_site_id)
    existing = {c.id for c in load_seeds(datasets_dir, name)}
    fresh = [c for c in normalised if c.id not in existing]
    if not fresh:
        return []
    # Serialise via ``model_dump(mode="json")`` so e.g. enum-ish ``source``
    # values round-trip cleanly through the JSONL store.
    append_cases(datasets_dir, name, (c.model_dump(mode="json") for c in fresh))
    return fresh


__all__ = ["load_seeds", "normalize_seeds", "save_seeds"]
