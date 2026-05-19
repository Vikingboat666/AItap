"""Critique-and-revise for the Wave 4 self-iteration loop.

The critic turns aggregated feedback — per-case :class:`JudgeScore` from
the LLM judge plus optional user thumbs / notes — into a new prompt
template. Three modes live behind a single :func:`revise` entry point so
the loop orchestrator (``iterate/loop.py``, owned by ``wt/loop``) treats
auto / guided / manual rewrites uniformly:

- ``auto``   — critic LLM rewrites the template freely.
- ``guided`` — critic LLM rewrites under a user-supplied instruction
  (e.g. "make the tone more professional").
- ``manual`` — no LLM; the user provides the full new template via the
  UI editor.

This module only produces a :class:`RevisedPrompt` value. It deliberately
**does not** write to ``prompt_versions`` or ``iterations``; that is
``wt/loop``'s transaction boundary. Keeping the persistence layer out of
the critic avoids the two-writer race that would otherwise appear when
the loop later wraps the whole round (insert iteration row + new version
row) in a single atomic block.

Provider-agnostic by construction: the only LLM dependency is
:class:`~aitap.deep.client.LLMClient`. Tests pin behaviour with
:class:`~aitap.deep.testing.MockLLMClient`, so the suite is offline.

Determinism
-----------
The critic LLM call uses ``temperature=0``. We want a critique to land
the same way every time given the same feedback — flicker in the critic
output makes convergence detection (delta-from-baseline, stagnation
window) flaky, which is exactly the failure mode the iteration loop is
supposed to eliminate.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aitap.deep.client import ChatMessage
from aitap.iterate.judge_models import Dimension, JudgeScore

if TYPE_CHECKING:
    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite


_LOGGER = logging.getLogger(__name__)


# Type aliases — narrow Literal so callers get autocompletion and pyright
# strict catches typo'd mode strings at the call site.
ReviseMode = Literal["auto", "guided", "manual"]
ThumbsValue = Literal["up", "down"]


_MANUAL_RATIONALE = "manual edit by user"


# --------------------------------------------------------------------------- #
# Public models                                                               #
# --------------------------------------------------------------------------- #


class AggregatedFeedback(BaseModel):
    """Critic input: judge per-case scores plus user thumbs / free-text notes.

    ``judge_scores`` is one :class:`JudgeScore` per case, in the same
    order as the run's outputs JSONL sidecar. ``user_thumbs`` and
    ``user_notes`` are indexed by case_index so the critic can correlate
    them with the judge findings (e.g. "thumbs-down on case 3 + judge
    flagged accuracy on case 3 — both signals point at hallucination").

    A :class:`JudgeScore` whose ``critique`` is the empty string is
    treated by the critic aggregator as a judge failure (the wt/judge
    review nit sentinel) — its critique is skipped when building the
    LLM prompt, but the score itself still counts toward the weighted
    aggregation.
    """

    model_config = ConfigDict(frozen=True)

    judge_scores: list[JudgeScore] = Field(default_factory=list)
    user_thumbs: dict[int, ThumbsValue] = Field(default_factory=dict)
    user_notes: dict[int, str] = Field(default_factory=dict)


class RevisedPrompt(BaseModel):
    """Critic output: the new template plus mode metadata for the loop.

    ``template_text`` is the verbatim new prompt body that
    ``wt/loop`` will hand to :func:`aitap.store.history.record_version`.

    ``mode`` is one of ``auto`` / ``guided`` / ``manual``. The loop
    persists it onto ``iterations.revise_mode`` so the history view can
    distinguish an LLM-driven rewrite from a human edit.

    ``instruction`` is the user's guidance string in guided mode; ``None``
    in auto and manual modes. We mirror it onto the output (rather than
    relying on the caller to remember) so the loop can write it to
    ``iterations.revise_instruction`` without re-threading state.

    ``rationale`` is human-readable provenance. For ``auto`` / ``guided``
    it is the critic LLM's explanation; for ``manual`` it is the fixed
    string ``"manual edit by user"``.
    """

    model_config = ConfigDict(frozen=True)

    template_text: str
    mode: ReviseMode
    instruction: str | None = None
    rationale: str


class CriticError(RuntimeError):
    """Raised when the critic LLM reply cannot be parsed.

    We surface this to the caller (the loop) rather than silently
    returning a no-op rewrite because the loop needs to decide whether to
    retry, fall back to manual mode, or abort the session. Swallowing
    the error here would let a broken critic chain produce identical
    rewrites round after round and the convergence detector would never
    notice.
    """


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


async def revise(
    *,
    prompt: PromptSite,
    current_template: str,
    feedback: AggregatedFeedback,
    mode: ReviseMode,
    client: LLMClient | None = None,
    instruction: str | None = None,
    manual_text: str | None = None,
    dimensions: list[Dimension] | None = None,
) -> RevisedPrompt:
    """Produce a :class:`RevisedPrompt` for *prompt* under one of three modes.

    Parameters
    ----------
    prompt:
        The :class:`PromptSite` being revised. Carried for grounding only —
        the critic uses ``prompt.name`` / ``prompt.purpose`` in its user
        prompt; it does NOT mutate the ``PromptSite``.
    current_template:
        The verbatim text of the prompt version under revision. This is the
        only template the critic LLM sees as input; do not preprocess it.
    feedback:
        Aggregated judge scores + user thumbs / notes for the most recent
        round. Empty critiques are skipped silently (judge failure
        sentinel) but the corresponding score still influences the
        weakest-dimension calculation.
    mode:
        ``"auto"`` — critic LLM rewrites freely.
        ``"guided"`` — critic LLM rewrites under *instruction*.
        ``"manual"`` — no LLM; *manual_text* becomes the new template.
    client:
        Required for ``auto`` and ``guided``; ignored for ``manual``.
    instruction:
        Required for ``guided``; ignored otherwise.
    manual_text:
        Required for ``manual``; ignored otherwise.
    dimensions:
        Optional list of :class:`Dimension`. When provided, the aggregator
        uses the weights to surface the weakest axis. When ``None``, the
        aggregator infers dimensions from ``feedback.judge_scores[*]
        .per_dim`` keys with equal weight — a graceful degradation when
        the caller didn't thread the configured rubric through.

    Returns
    -------
    A :class:`RevisedPrompt`. The caller (``wt/loop``) is responsible for
    persisting the new template via
    :func:`aitap.store.history.record_version` and recording the
    iteration row.

    Raises
    ------
    ValueError
        When the mode-specific required inputs are missing
        (``auto``/``guided`` without ``client``; ``guided`` without
        ``instruction``; ``manual`` without ``manual_text``).
    CriticError
        When the critic LLM reply cannot be parsed as ``{revised_template,
        rationale}`` JSON. The loop catches this to decide retry / abort.
    """
    if mode == "manual":
        return _revise_manual(manual_text=manual_text)

    if mode == "auto":
        if client is None:
            raise ValueError("auto mode requires an LLMClient")
        return await _revise_with_llm(
            prompt=prompt,
            current_template=current_template,
            feedback=feedback,
            mode="auto",
            client=client,
            instruction=None,
            dimensions=dimensions,
        )

    # mode == "guided"
    if client is None:
        raise ValueError("guided mode requires an LLMClient")
    if instruction is None or not instruction.strip():
        raise ValueError("guided mode requires an instruction")
    return await _revise_with_llm(
        prompt=prompt,
        current_template=current_template,
        feedback=feedback,
        mode="guided",
        client=client,
        instruction=instruction,
        dimensions=dimensions,
    )


# --------------------------------------------------------------------------- #
# Mode implementations                                                        #
# --------------------------------------------------------------------------- #


def _revise_manual(*, manual_text: str | None) -> RevisedPrompt:
    """Wrap a user-provided new template as a :class:`RevisedPrompt`.

    Zero LLM cost on this path — the contract is the user already
    edited the prompt in the UI and we are just persisting their text.
    The rationale is a fixed sentinel so the history view can render
    "manual edit" without invoking the rationale parser.
    """
    if manual_text is None:
        raise ValueError("manual mode requires manual_text")
    return RevisedPrompt(
        template_text=manual_text,
        mode="manual",
        instruction=None,
        rationale=_MANUAL_RATIONALE,
    )


async def _revise_with_llm(
    *,
    prompt: PromptSite,
    current_template: str,
    feedback: AggregatedFeedback,
    mode: Literal["auto", "guided"],
    client: LLMClient,
    instruction: str | None,
    dimensions: list[Dimension] | None,
) -> RevisedPrompt:
    """Drive the critic LLM and parse its reply into a :class:`RevisedPrompt`.

    The user prompt is built block-by-block (purpose, current template,
    aggregated critique, optional instruction, output contract) so the
    same input always produces the same prompt layout. Layout drift in
    the critic prompt drifts the rewrite, which drifts the convergence
    detector — boring assembly is the goal.
    """
    resolved_dims = _resolve_dimensions(dimensions, feedback)
    system = _system_prompt()
    user = _build_user_prompt(
        prompt=prompt,
        current_template=current_template,
        feedback=feedback,
        dimensions=resolved_dims,
        instruction=instruction,
    )

    response = await client.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ],
        temperature=0.0,
        max_tokens=2048,
        response_format="json",
    )

    template, critic_rationale = _parse_critic_reply(response.text)
    rationale = _compose_rationale(
        mode=mode,
        instruction=instruction,
        critic_rationale=critic_rationale,
    )
    return RevisedPrompt(
        template_text=template,
        mode=mode,
        instruction=instruction,
        rationale=rationale,
    )


# --------------------------------------------------------------------------- #
# Prompt construction                                                         #
# --------------------------------------------------------------------------- #


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    """Read the critic system prompt from the bundled prompts dir.

    Mirrors :func:`aitap.iterate.judge._system_prompt` so an installed
    wheel can serve the file via :mod:`importlib.resources` while the
    in-tree dev path falls back to the on-disk file.
    """
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.iterate.prompts").joinpath("critic.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            here = Path(__file__).resolve().parent / "prompts" / "critic.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


def _build_user_prompt(
    *,
    prompt: PromptSite,
    current_template: str,
    feedback: AggregatedFeedback,
    dimensions: list[Dimension],
    instruction: str | None,
) -> str:
    """Assemble the critic user prompt from fixed blocks.

    The blocks are joined with two newlines and labelled explicitly so a
    human inspecting the LLM transcript can tell at a glance which input
    drove which rewrite. The "Output contract" trailer is a deliberate
    repetition of the system prompt's JSON spec — model behaviour
    improves when the output schema appears at both the system and user
    sides of the call.
    """
    parts: list[str] = []
    purpose = prompt.purpose or "(no purpose statement available)"
    parts.append(f"Prompt name: {prompt.name}")
    parts.append(f"Prompt purpose: {purpose}")

    parts.append("Current template (this is what you may rewrite):")
    parts.append(current_template)

    parts.append("Aggregated judge feedback and user signals:")
    parts.append(_aggregate_critique_for_llm(feedback, dimensions))

    dim_payload = [{"name": d.name, "weight": d.weight} for d in dimensions]
    parts.append("Dimension weights (bigger weight = bigger impact on total):")
    parts.append(json.dumps(dim_payload, ensure_ascii=False, indent=2))

    if instruction is not None:
        parts.append("User instruction (you MUST follow this direction):")
        parts.append(instruction)

    parts.append(
        "Respond with a single JSON object: "
        '{"revised_template": "...", "rationale": "..."}. '
        "No prose, no markdown, no code fences."
    )
    return "\n\n".join(parts)


def _aggregate_critique_for_llm(
    feedback: AggregatedFeedback,
    dimensions: list[Dimension],
) -> str:
    """Summarise per-case feedback into a single readable block.

    The block surfaces:

    - The 1-2 lowest-scoring dimensions across cases (weight-aware: a
      0.2 score on a 0.4-weight dim is worse than a 0.2 score on a
      0.05-weight dim).
    - The critique text of the 1-2 worst cases (skipping any case whose
      critique is the empty string — that is the judge-failure sentinel
      from ``wt/judge``).
    - The user thumbs-down notes, one per affected case.

    Returned text is non-empty by construction (the trailing line is
    always emitted) so the critic prompt builder can drop it into the
    template without conditional logic.

    Public for testing — exported on ``__all__`` and exercised
    independently of :func:`revise` so the aggregator's edge cases
    (judge failure sentinels, empty notes) don't have to be probed via
    the LLM transcript.
    """
    lines: list[str] = []

    weak = _weakest_dimensions(feedback.judge_scores, dimensions)
    if weak:
        joined = ", ".join(weak)
        lines.append(f"Lowest-scoring dimension(s) across cases: {joined}.")

    worst_critiques = _select_worst_critiques(feedback.judge_scores, dimensions, limit=2)
    if worst_critiques:
        lines.append("Worst-case judge critiques:")
        for case_idx, critique in worst_critiques:
            lines.append(f"- case {case_idx}: {critique}")

    thumbs_down_notes = _select_thumbs_down_notes(feedback)
    if thumbs_down_notes:
        lines.append("User flagged the following cases (thumbs-down):")
        for case_idx, note in thumbs_down_notes:
            shown = note if note else "(no note)"
            lines.append(f"- case {case_idx}: {shown}")

    extra_notes = _select_notes_without_thumbs(feedback)
    if extra_notes:
        lines.append("Additional user notes:")
        for case_idx, note in extra_notes:
            lines.append(f"- case {case_idx}: {note}")

    if not lines:
        # Defensive: the critic still needs *some* signal — give it an
        # explicit "no critiques captured" marker rather than a blank
        # string so the LLM does not hallucinate one to fill the void.
        lines.append("No specific critiques captured for this round.")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reply parsing                                                               #
# --------------------------------------------------------------------------- #


def _parse_critic_reply(text: str) -> tuple[str, str]:
    """Pull ``revised_template`` + ``rationale`` out of the critic reply.

    Tolerates code fences and leading / trailing prose (mirrors the
    judge's parser). Raises :class:`CriticError` when the reply isn't a
    JSON object or is missing the ``revised_template`` field — the
    rationale is best-effort and falls back to a sentinel when absent.
    """
    payload = _extract_json_object(text)
    if payload is None:
        _LOGGER.warning("critic reply was not parseable as JSON")
        raise CriticError("critic reply was not parseable as JSON")

    template_raw = payload.get("revised_template")
    if not isinstance(template_raw, str) or not template_raw.strip():
        _LOGGER.warning("critic reply missing non-empty 'revised_template' string")
        raise CriticError("critic reply missing 'revised_template'")

    rationale_raw = payload.get("rationale")
    rationale = (
        rationale_raw.strip()
        if isinstance(rationale_raw, str) and rationale_raw.strip()
        else "(critic did not provide a rationale)"
    )
    return template_raw, rationale


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Slice a JSON object out of *text* and return it as a dict.

    Same shape as :func:`aitap.iterate.judge._extract_json_object`; we
    duplicate it instead of importing because the judge module currently
    exposes it as a private helper and reaching across the contract
    would couple the two modules' reply formats. If a third consumer
    appears, lift this into ``deep/json_utils.py``.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    if not cleaned.startswith("{"):
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first == -1 or last == -1 or last <= first:
            return None
        cleaned = cleaned[first : last + 1]

    try:
        loaded: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    out: dict[str, Any] = {}
    for key, value in loaded.items():
        if isinstance(key, str):
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# Helpers — dimensions, ranking, rationale composition                        #
# --------------------------------------------------------------------------- #


def _resolve_dimensions(
    dimensions: list[Dimension] | None,
    feedback: AggregatedFeedback,
) -> list[Dimension]:
    """Pick a dimension list to use when the caller didn't pass one.

    When ``dimensions`` is ``None`` we fall back to the union of dim
    names found across the scored cases, each with equal weight. This is
    strictly worse than the configured rubric (weights are flat) but it
    keeps :func:`revise` callable without forcing every caller to wire
    in ``load_dimensions``. ``wt/loop`` is expected to pass the real
    rubric in production.
    """
    if dimensions is not None:
        return dimensions
    seen: dict[str, None] = {}
    for s in feedback.judge_scores:
        for name in s.per_dim:
            seen.setdefault(name, None)
    if not seen:
        return []
    weight = 1.0 / len(seen)
    return [Dimension(name=n, weight=weight, rubric="(inferred)") for n in seen]


def _weakest_dimensions(
    scores: list[JudgeScore],
    dimensions: list[Dimension],
    *,
    limit: int = 2,
) -> list[str]:
    """Return up to *limit* dimension names with the worst weighted impact.

    "Worst weighted impact" = ``(1 - mean_score) * weight`` — a low
    score on a high-weight dim moves the weighted total more than the
    same low score on a low-weight dim, so we rank by the gap a fix
    would close, not by the raw score.
    """
    if not scores or not dimensions:
        return []

    impacts: list[tuple[float, str]] = []
    for dim in dimensions:
        values = [s.per_dim.get(dim.name) for s in scores if dim.name in s.per_dim]
        observed = [v for v in values if v is not None]
        if not observed:
            continue
        mean = sum(observed) / len(observed)
        gap_impact = (1.0 - mean) * dim.weight
        impacts.append((gap_impact, dim.name))

    if not impacts:
        return []

    # Sort by gap-impact descending; tiebreak by name to keep output
    # stable across Python dict-order changes.
    impacts.sort(key=lambda x: (-x[0], x[1]))
    # Drop dims that already look perfect — surfacing them as "weakest"
    # would mislead the critic into editing something fine.
    interesting = [name for impact, name in impacts if impact > 0.0]
    if not interesting:
        return []
    return interesting[:limit]


def _select_worst_critiques(
    scores: list[JudgeScore],
    dimensions: list[Dimension],
    *,
    limit: int = 2,
) -> list[tuple[int, str]]:
    """Pick the *limit* cases with the lowest weighted_total + non-empty critique.

    Empty critique is the judge-failure sentinel from ``wt/judge`` —
    those rows are skipped so the critic doesn't see fabricated commentary
    in place of a real failure mode. ``dimensions`` is currently unused
    but kept in the signature so a future ranking that weights by
    dim-coverage doesn't churn callers.
    """
    del dimensions  # reserved for future weighting
    indexed: list[tuple[int, JudgeScore]] = list(enumerate(scores))
    with_critique = [(i, s) for i, s in indexed if s.critique]
    with_critique.sort(key=lambda pair: pair[1].weighted_total)
    return [(i, s.critique) for i, s in with_critique[:limit]]


def _select_notes_without_thumbs(
    feedback: AggregatedFeedback,
) -> list[tuple[int, str]]:
    """Return user notes that did not come with a thumbs-down signal.

    A note attached to a thumbs-down is already surfaced under the
    flagged-cases block; surfacing it twice would weight the critic's
    attention toward the same line of feedback. Notes without a thumbs
    (e.g. the user typed a comment but did not vote) still belong in
    the prompt — they carry intent the judge can't see.
    """
    out: list[tuple[int, str]] = []
    for case_idx in sorted(feedback.user_notes):
        if case_idx in feedback.user_thumbs:
            continue
        note = feedback.user_notes[case_idx]
        if not note:
            continue
        out.append((case_idx, note))
    return out


def _select_thumbs_down_notes(
    feedback: AggregatedFeedback,
) -> list[tuple[int, str]]:
    """Return (case_index, note) pairs for every thumbs-down case.

    Sorted by case index so the surfaced ordering is deterministic. The
    user's note may legitimately be empty (they tapped thumbs-down
    without typing) — we still surface the case index because the bare
    signal is itself feedback.
    """
    out: list[tuple[int, str]] = []
    for case_idx in sorted(feedback.user_thumbs):
        if feedback.user_thumbs[case_idx] != "down":
            continue
        note = feedback.user_notes.get(case_idx, "")
        out.append((case_idx, note))
    return out


def _compose_rationale(
    *,
    mode: Literal["auto", "guided"],
    instruction: str | None,
    critic_rationale: str,
) -> str:
    """Glue the user instruction (guided) onto the critic's rationale.

    For ``auto`` we surface the critic's rationale verbatim. For
    ``guided`` we prefix the user's instruction so the history view
    can show "what you asked for" alongside "what the critic did".
    """
    if mode == "guided" and instruction is not None:
        return f"User instruction: {instruction}\nCritic: {critic_rationale}"
    return critic_rationale


__all__ = [
    "AggregatedFeedback",
    "CriticError",
    "ReviseMode",
    "RevisedPrompt",
    "_aggregate_critique_for_llm",
    "revise",
]
