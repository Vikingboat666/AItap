"""Ask an :class:`LLMClient` to generate variant test cases from seeds.

This is the L1 step of the test-case strategy: the user writes 2-3 seed
cases by hand, the LLM expands them into boundary / adversarial / noise
variants using the prompt's purpose (from L2 ``purpose_inferer``) and an
optional :class:`InputShape` hint (from :mod:`aitap.dataset.code_context`)
as grounding.

The expander never touches a provider SDK directly — it talks to the
:class:`LLMClient` abstract contract so tests pass with
:class:`aitap.deep.testing.MockLLMClient` and offline CI stays green.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.dataset.types import Case, CaseSource, InputShape, case_id
from aitap.deep.client import ChatMessage

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from aitap.deep.client import LLMClient


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    """Read the case-expander system prompt from the bundled prompts dir.

    Mirrors :func:`aitap.deep.purpose_inferer._system_prompt`: prefer
    ``importlib.resources`` (works in installed wheels) and fall back to a
    source-tree path so worktree-time tests pass before any install.
    """
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.deep.prompts").joinpath("case_expander.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            here = Path(__file__).resolve().parent.parent / "deep" / "prompts" / "case_expander.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


async def expand(
    seeds: Iterable[Case | Mapping[str, Any]],
    count: int,
    client: LLMClient,
    prompt_purpose: str | None = None,
    *,
    prompt_site_id: str | None = None,
    input_shape: InputShape | None = None,
    source: CaseSource = "expand",
) -> list[Case]:
    """Generate *count* new :class:`Case` rows from *seeds* via the LLM.

    Parameters
    ----------
    seeds:
        Hand-authored cases (or loose dicts in the same shape). At least one
        is required — the LLM needs the input shape to copy.
    count:
        How many cases to generate. The result list is *always* exactly this
        long; if the model returns fewer, we pad with safe ``boundary``
        fallback variants so callers can rely on the length contract.
    client:
        Any :class:`LLMClient`. Tests pass :class:`MockLLMClient`.
    prompt_purpose:
        Output of L2 ``purpose_inferer``; helps the model write semantically
        relevant inputs.
    prompt_site_id:
        Sets the generated cases' ``prompt_site_id``. Defaults to the seeds'
        site id when seeds carry one, else an empty string (allowed because
        cases without a site id are still valid — the dataset editor will
        bind them when saving).
    input_shape:
        Optional :class:`InputShape` grounding from
        :func:`aitap.dataset.code_context.infer_input_shape`.
    source:
        Provenance tag attached to generated cases. Defaults to ``"expand"``
        but the orchestrator overrides it for the ``"context"`` mode.

    Returns
    -------
    A list of *count* :class:`Case` rows, each with a stable id from
    :func:`case_id` so re-running with the same seeds + a deterministic
    mock client produces identical output.
    """
    if count <= 0:
        return []

    seed_cases: list[Case] = _coerce_seeds(seeds, prompt_site_id=prompt_site_id or "")
    if not seed_cases:
        raise ValueError("expand() requires at least one seed case to copy the input shape from")

    resolved_site_id = prompt_site_id or seed_cases[0].prompt_site_id

    user_block = _build_user_prompt(seed_cases, count, prompt_purpose, input_shape)
    response = await client.chat(
        [
            ChatMessage(role="system", content=_system_prompt()),
            ChatMessage(role="user", content=user_block),
        ],
        temperature=0.7,
        max_tokens=2048,
        response_format="json",
    )

    parsed = _parse_cases(response.text)
    seen: set[str] = {c.id for c in seed_cases}
    out: list[Case] = []
    for raw_case in parsed:
        if len(out) >= count:
            break
        case = _build_case(
            raw_case, resolved_site_id, fallback_inputs=seed_cases[0].inputs, source=source
        )
        if case is None or case.id in seen:
            continue
        seen.add(case.id)
        out.append(case)

    # Pad if the LLM under-delivered — the caller asked for exactly N.
    while len(out) < count:
        fallback = _fallback_case(
            seed_cases[0], index=len(out), site_id=resolved_site_id, source=source
        )
        if fallback.id in seen:
            # Tweak the notes so the id changes for the next iteration. We
            # already include the index in the inputs payload so this loop
            # cannot run forever.
            fallback = fallback.model_copy(
                update={
                    "id": case_id(
                        resolved_site_id,
                        {**fallback.inputs, "_fill_index": len(out)},
                    )
                }
            )
        seen.add(fallback.id)
        out.append(fallback)

    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _coerce_seeds(seeds: Iterable[Case | Mapping[str, Any]], *, prompt_site_id: str) -> list[Case]:
    # Local import to avoid a tiny circular concern at module-load time:
    # ``aitap.dataset.__init__`` imports from this module.
    from aitap.dataset.seed import normalize_seeds

    return normalize_seeds(seeds, prompt_site_id=prompt_site_id)


def _build_user_prompt(
    seeds: list[Case],
    count: int,
    purpose: str | None,
    shape: InputShape | None,
) -> str:
    parts: list[str] = [f"Generate {count} new test cases."]
    if purpose:
        parts.append(f"Prompt purpose: {purpose}")
    if shape is not None and not shape.is_empty():
        parts.append(f"Input shape (from surrounding code): {shape.model_dump_json()}")
    parts.append("Seed cases:")
    parts.append(
        json.dumps(
            [{"inputs": s.inputs, "tags": s.tags, "notes": s.notes} for s in seeds],
            ensure_ascii=False,
        )
    )
    parts.append(
        f"Return ONLY a JSON array of exactly {count} cases with keys 'inputs', 'tags', 'notes'."
    )
    return "\n\n".join(parts)


def _parse_cases(text: str) -> list[dict[str, Any]]:
    """Pull a JSON array of case dicts from the model's reply.

    Tolerates leading/trailing prose and ```json fences — every model we've
    profiled adds at least one of those even when told not to.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    # If there's still trailing prose, try to slice from the first '[' to the
    # matching last ']'. This is a heuristic but it covers the common case
    # of "Here is the JSON: [...]." trailing junk.
    if not cleaned.startswith("["):
        first = cleaned.find("[")
        last = cleaned.rfind("]")
        if first != -1 and last != -1 and last > first:
            cleaned = cleaned[first : last + 1]

    try:
        raw: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def _build_case(
    raw: dict[str, Any],
    site_id: str,
    *,
    fallback_inputs: dict[str, object],
    source: CaseSource,
) -> Case | None:
    """Turn one parsed dict from the LLM response into a :class:`Case`.

    The LLM is given a strict schema but real models drift — we accept a
    case as long as it has *some* dict-shaped inputs. Other fields default
    to safe values.
    """
    inputs_raw: Any = raw.get("inputs")
    if isinstance(inputs_raw, dict):
        inputs_dict: dict[Any, Any] = inputs_raw
        inputs: dict[str, object] = {
            str(k): v for k, v in inputs_dict.items() if isinstance(k, str)
        }
    else:
        # Last-ditch: some models forget the wrapper and put kwargs at the
        # top level. Strip the metadata keys and treat the rest as inputs.
        meta = {"tags", "notes", "expected"}
        inputs = {k: v for k, v in raw.items() if k not in meta}

    if not inputs:
        # Refuse cases with no inputs — they're useless to the runner and
        # would all collide on the empty-dict id.
        return None

    raw_tags: Any = raw.get("tags", [])
    tags: list[str] = (
        [str(t) for t in raw_tags if isinstance(t, str)] if isinstance(raw_tags, list) else []
    )
    notes_raw: Any = raw.get("notes")
    notes = str(notes_raw) if isinstance(notes_raw, str) and notes_raw.strip() else None
    expected_raw: Any = raw.get("expected")
    expected = str(expected_raw) if isinstance(expected_raw, str) and expected_raw else None

    # Guard against missing slots — fill from fallback_inputs so the row is
    # still runnable end-to-end. The dataset editor surfaces tags so users
    # can sanity-check.
    for key in fallback_inputs:
        inputs.setdefault(key, fallback_inputs[key])

    return Case(
        id=case_id(site_id, inputs),
        prompt_site_id=site_id,
        inputs=inputs,
        expected=expected,
        tags=tags,
        source=source,
        notes=notes,
    )


def _fallback_case(seed: Case, *, index: int, site_id: str, source: CaseSource) -> Case:
    """Build a deterministic placeholder case when the LLM under-delivers.

    We mutate string values in the seed to obvious boundary variants
    (empty, very long) keyed by *index* so each fill is distinct. Non-string
    values are passed through unchanged; the fallback is a safety net, not
    a creative generator.
    """
    inputs = dict(seed.inputs)
    variants = [
        ("", "boundary-empty"),
        ("a", "boundary-single-char"),
        ("   ", "boundary-whitespace"),
        ("x" * 4000, "boundary-long"),
    ]
    fill_value, note = variants[index % len(variants)]

    str_keys = [k for k, v in inputs.items() if isinstance(v, str)]
    if str_keys:
        target = str_keys[0]
        inputs[target] = fill_value
    return Case(
        id=case_id(site_id, inputs),
        prompt_site_id=site_id,
        inputs=inputs,
        tags=["boundary", "auto-fallback"],
        source=source,
        notes=f"auto-fallback ({note}) — model returned fewer cases than requested",
    )


__all__ = ["expand"]
