"""LLM-as-judge for the Wave 4 self-iteration loop.

The judge reads per-case outputs produced by the playground runner
(``.aitap/runs/<id>/outputs.jsonl`` — written by :mod:`aitap.playground.dispatch`)
and grades each one along a list of :class:`Dimension`. The result is a
:class:`JudgeScore` per case that the loop orchestrator (M4
``iterate/loop.py``, owned by ``wt/loop``) uses to:

- decide whether to keep iterating (convergence vs. stagnation);
- hand to the critic (``wt/critic``) as targeted feedback for the rewriter;
- persist on the ``scores`` table (here) and the future ``iterations`` table
  (``wt/iterations-store``).

Provider-agnostic by construction: the only LLM dependency is
:class:`~aitap.deep.client.LLMClient`. Tests pin this behaviour with
:class:`~aitap.deep.testing.MockLLMClient`.

One LLM call per case
---------------------
The judge scores each case in its own LLM call. Batching every case into
one mega-prompt was tempting (cheaper) but rejected because:

- per-case independence keeps a single bad case from poisoning the score
  of its siblings (the failure mode we exercise in
  ``test_score_outputs_unparseable_response_yields_zero_score``);
- per-case grounding (input + expected) is cleaner than threading 50
  cases through one judge prompt;
- per-case retries are trivial if we add them later.

Cost is bounded by ``len(outputs)`` calls per round; the run-level cost
extras already piggy-back on the sidecar so the critic can prioritise
high-cost cases without re-running the judge.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aitap.deep.client import ChatMessage
from aitap.iterate.judge_defaults import DEFAULT_DIMENSIONS
from aitap.iterate.judge_models import Dimension, JudgeScore
from aitap.store import runs as runs_dao

if TYPE_CHECKING:
    from aitap.config import Settings
    from aitap.deep.client import LLMClient


_LOGGER = logging.getLogger(__name__)


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    """Read the judge system prompt from the bundled prompts dir.

    Mirrors the resource-then-source-tree fallback used by
    :func:`aitap.dataset.llm_expander._system_prompt`: installed wheels
    serve the file via :mod:`importlib.resources`; in-tree development
    falls back to the on-disk path so unit tests work before any install.
    """
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.iterate.prompts").joinpath("judge.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            here = Path(__file__).resolve().parent / "prompts" / "judge.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def score_outputs(
    *,
    site_purpose: str,
    outputs: list[dict[str, Any]],
    dimensions: list[Dimension],
    client: LLMClient,
    reference: dict[str, Any] | None = None,
) -> list[JudgeScore]:
    """Score each per-case output along *dimensions* and return a JudgeScore list.

    Parameters
    ----------
    site_purpose:
        Human-readable summary of the prompt under test (typically the
        output of L2 ``purpose_inferer``). Passed verbatim into the judge
        user prompt so the judge has task grounding.
    outputs:
        Per-case output dicts as loaded from the JSONL sidecar via
        ``json.loads(line)``. **Do not** push them through
        ``RunOutput.model_validate`` — the sidecar carries forward-looking
        ``cost_usd`` / ``usage`` / ``latency_ms`` extras that the M3
        contract drops on validation, and the critic worktree will need
        them downstream. See ``CONTRACTS.md`` and the runs-persistence PR
        review for the explicit warning.
    dimensions:
        Resolved active dimensions (call :func:`load_dimensions` first if
        you want the project/per-prompt override stack).
    client:
        Any :class:`LLMClient`. Tests pass :class:`MockLLMClient`.
    reference:
        Optional ideal answer / rule set. When provided, the keys + values
        are serialised into the user-side judge prompt as a "Reference"
        block; the judge weighs the output against the reference for the
        accuracy dimension. Pass ``None`` (the default) when no gold
        answer is available — the judge still scores against rubric and
        purpose, just with weaker grounding.

    Returns
    -------
    One :class:`JudgeScore` per element of *outputs*, in the same order.
    A zero-length input yields ``[]`` without invoking the LLM.
    """
    if not outputs:
        return []

    system = _system_prompt()
    scores: list[JudgeScore] = []
    for output in outputs:
        user_block = _build_user_prompt(
            site_purpose=site_purpose,
            output=output,
            dimensions=dimensions,
            reference=reference,
        )
        try:
            response = await client.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user_block),
                ],
                temperature=0.0,
                max_tokens=512,
                response_format="json",
            )
        except Exception:
            # A transport-level failure for one case should not poison the
            # whole batch — log it and emit a zero score so the loop can
            # still converge / stagnate on partial data.
            _LOGGER.exception("judge LLM call failed; emitting zero score for this case")
            scores.append(_zero_score(dimensions))
            continue

        scores.append(_parse_judge_reply(response.text, dimensions))
    return scores


def load_dimensions(
    settings: Settings,
    prompt_id: str | None = None,
) -> list[Dimension]:
    """Resolve the active scoring dimensions through the three-layer override.

    Precedence (later layers replace earlier ones wholesale — we do not
    merge dimension lists, because mixing weights across layers would
    silently rebalance the total in a way users cannot debug):

    1. :data:`DEFAULT_DIMENSIONS` — the canonical 4-axis rubric (Wave 4
       design Decision 1).
    2. ``.aitap/config.yaml`` ``judge.dimensions`` — project-level
       override.
    3. ``.aitap/prompts/<prompt_id>.prompt.yaml`` ``judge_dimensions`` —
       per-prompt override, applied when *prompt_id* is provided and
       the prompt yaml exists.

    Errors at any layer (file missing, malformed yaml, validation failure)
    fall back to the next-most-specific layer with a warning log. We never
    raise from this function — the worst case is "judge runs with the
    defaults", which is strictly better than "iterate command crashes".
    """
    # Layer 3: per-prompt
    if prompt_id is not None:
        per_prompt = _load_per_prompt_dimensions(settings, prompt_id)
        if per_prompt is not None:
            return per_prompt

    # Layer 2: project
    project = _load_project_dimensions(settings)
    if project is not None:
        return project

    # Layer 1: default
    return DEFAULT_DIMENSIONS


def persist_judge_scores(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    scores: list[JudgeScore],
    judge_name: str,
) -> None:
    """Write one ``scores`` row per case under ``judge_kind='llm'``.

    The existing :func:`aitap.store.runs.insert_score` already accepts the
    LLM-judge shape (``judge_kind='llm'``, ``judge_name=<model id>``,
    ``score=<float>``, ``rationale=<critique>``). We deliberately do not
    extend the schema with per-dimension columns: that work lives on the
    ``iterations`` table (``wt/iterations-store`` owns
    ``per_dim_scores TEXT``), and duplicating it here would create two
    sources of truth.

    The caller is responsible for the surrounding transaction. We do not
    wrap one inside this helper because batch-judge persistence is one
    leaf of a larger atomic unit (insert iteration row + scores rows in
    one shot) that the loop orchestrator will own.
    """
    for case_index, score in enumerate(scores):
        runs_dao.insert_score(
            conn,
            run_id=run_id,
            case_index=case_index,
            judge_kind="llm",
            judge_name=judge_name,
            score=score.weighted_total,
            rationale=score.critique or None,
        )


# ---------------------------------------------------------------------------
# Internals: judge prompt construction
# ---------------------------------------------------------------------------


def _build_user_prompt(
    *,
    site_purpose: str,
    output: dict[str, Any],
    dimensions: list[Dimension],
    reference: dict[str, Any] | None,
) -> str:
    """Build the user-side judge prompt for one case.

    We assemble the prompt in fixed blocks (purpose, dimensions, output,
    optional reference, final ask) so the judge sees the same structure
    every call — drift in the prompt layout drifts the scores, which
    drifts the convergence detector, which manifests as flaky tests
    months later. Keep this assembly boring on purpose.
    """
    parts: list[str] = []
    parts.append(f"Prompt purpose under test: {site_purpose}")

    # Dimension rubrics — JSON for easy machine grounding, indented for
    # readability when a human inspects the LLM transcript.
    dim_payload = [{"name": d.name, "weight": d.weight, "rubric": d.rubric} for d in dimensions]
    parts.append("Scoring dimensions (assign a [0.0, 1.0] score to each):")
    parts.append(json.dumps(dim_payload, ensure_ascii=False, indent=2))

    # The output under test — pull only the human-meaningful fields so
    # the judge does not see usage/cost noise it would try to interpret.
    parts.append("Output under test:")
    parts.append(_format_output_for_judge(output))

    if reference is not None and reference:
        parts.append("Reference (ideal answer / gold rules — weigh the output against this):")
        parts.append(json.dumps(reference, ensure_ascii=False, indent=2))

    dim_names = ", ".join(d.name for d in dimensions)
    parts.append(
        "Respond with a single JSON object containing a float in [0.0, 1.0] for "
        f"each of these keys: {dim_names}; plus a non-empty 'critique' string. "
        "No prose, no markdown, no code fences."
    )
    return "\n\n".join(parts)


def _format_output_for_judge(output: dict[str, Any]) -> str:
    """Render the output dict into the judge's user prompt.

    We surface ``text`` (the primary), ``error`` (so the judge knows the
    runner failed instead of trying to grade an empty string as if it
    were the model's actual answer), and ``intermediate`` (pipeline
    per-node trace). Forward-looking fields (``cost_usd``, ``usage``,
    ``latency_ms``) are intentionally omitted — the judge does not score
    cost, the critic uses them for prioritisation.
    """
    text = output.get("text")
    error = output.get("error")
    intermediate = output.get("intermediate")

    block: dict[str, Any] = {}
    if text is not None:
        block["text"] = text
    if error is not None:
        block["error"] = error
    if intermediate:
        block["intermediate"] = intermediate

    if not block:
        # Defensive: if the output dict literally has nothing useful, give
        # the judge an explicit marker rather than the empty string. The
        # judge can then score format=0 / accuracy=0 with grounding.
        block["text"] = ""
    return json.dumps(block, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Internals: judge reply parsing + aggregation
# ---------------------------------------------------------------------------


def _parse_judge_reply(text: str, dimensions: list[Dimension]) -> JudgeScore:
    """Turn the judge LLM's reply into a JudgeScore.

    The reply contract is a flat JSON object: ``{<dim>: <float>, ...,
    "critique": "..."}``. Real models drift — code fences, leading prose,
    trailing apologies — so we extract the first JSON object we can find
    and tolerate everything around it. On any parse failure we return a
    zero score so the loop can still progress; the warning surfaces in
    ``aitap.iterate.judge`` for operators grepping logs.
    """
    payload = _extract_json_object(text)
    if payload is None:
        _LOGGER.warning("judge reply was not parseable as JSON; using zero score")
        return _zero_score(dimensions)

    per_dim: dict[str, float] = {}
    for dim in dimensions:
        raw = payload.get(dim.name)
        per_dim[dim.name] = _coerce_score(raw)

    critique_raw = payload.get("critique")
    critique = str(critique_raw) if isinstance(critique_raw, str) else ""

    weighted_total = _aggregate(per_dim, dimensions)
    return JudgeScore(
        weighted_total=weighted_total,
        per_dim=per_dim,
        critique=critique,
    )


def _aggregate(per_dim: dict[str, float], dimensions: list[Dimension]) -> float:
    """Weighted sum across *dimensions*, clamped to [0, 1].

    Missing dims (already defaulted to 0 by :func:`_parse_judge_reply`)
    contribute 0 to the total. We clamp the final value because a
    miscalibrated user config (weights summing to >1, judge returning
    >1.0 by accident) should not produce out-of-range data on the
    persisted ``weighted_score`` column — that would break downstream
    delta-from-baseline math.
    """
    total = 0.0
    for dim in dimensions:
        total += per_dim.get(dim.name, 0.0) * dim.weight
    if total < 0.0:
        return 0.0
    if total > 1.0:
        return 1.0
    return total


def _zero_score(dimensions: list[Dimension]) -> JudgeScore:
    """Build a JudgeScore where every dimension is 0.

    Used when the LLM call errors or the reply cannot be parsed. We emit
    a zero score rather than raise so a single bad case does not abort
    the surrounding iteration round.
    """
    return JudgeScore(
        weighted_total=0.0,
        per_dim={d.name: 0.0 for d in dimensions},
        critique="",
    )


def _coerce_score(raw: Any) -> float:
    """Convert a judge-returned per-dim value into a float in [0.0, 1.0].

    Accepts ints, floats, and strings that look like floats. Anything
    else (None, dict, list) becomes 0.0. We clamp to [0, 1] rather than
    raise because the judge LLM occasionally emits 1.2 or -0.1; clipping
    is the least-surprising thing the convergence math can consume.
    """
    if isinstance(raw, bool):  # bool is a subclass of int; reject early
        return 1.0 if raw else 0.0
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Slice a JSON object out of *text* and return it as a dict.

    Tolerates leading prose, ```json fences, and trailing apologies the
    way :func:`aitap.dataset.llm_expander._parse_cases` does for arrays.
    Returns ``None`` when no parseable object is found.
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
    # Narrow Any-valued dict to str-keyed dict for downstream type safety.
    out: dict[str, Any] = {}
    for key, value in loaded.items():
        if isinstance(key, str):
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Internals: three-layer dimension override loading
# ---------------------------------------------------------------------------


def _load_project_dimensions(settings: Settings) -> list[Dimension] | None:
    """Read ``.aitap/config.yaml`` and pull ``judge.dimensions`` if present.

    Returns ``None`` when the file is absent or the key is missing — the
    caller falls back to defaults. Returns ``None`` on malformed yaml too
    (with a warning) for the same reason: a broken config should not
    crash an iteration run, the defaults are always reachable.
    """
    config_path = settings.project_root / settings.aitap_dir / "config.yaml"
    return _read_dimensions_from_yaml(config_path, key_path=("judge", "dimensions"))


def _load_per_prompt_dimensions(settings: Settings, prompt_id: str) -> list[Dimension] | None:
    """Read ``.aitap/prompts/<prompt_id>.prompt.yaml`` for ``judge_dimensions``."""
    prompt_path = settings.prompts_dir / f"{prompt_id}.prompt.yaml"
    return _read_dimensions_from_yaml(prompt_path, key_path=("judge_dimensions",))


def _read_dimensions_from_yaml(path: Path, *, key_path: tuple[str, ...]) -> list[Dimension] | None:
    """Load a list-of-mappings from *path* at *key_path* and validate as Dimensions.

    Any error path — file missing, yaml parse failure, key absent, list
    item not a mapping, Dimension validation failure — collapses to
    ``None`` so the caller falls back to the next layer. Validation
    failures log at WARNING so a user with a typo in the rubric weight
    can spot it without enabling debug logging.
    """
    if not path.exists():
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        _LOGGER.warning("could not parse %s as YAML; using next override layer", path)
        return None

    node: Any = loaded
    for key in key_path:
        if not isinstance(node, dict):
            return None
        # ``yaml.safe_load`` returns a fully untyped tree (the stub declares
        # ``Any`` at every level), and pyright strict treats ``dict.get`` on
        # an untyped dict as partially-unknown. Cast back to ``Any`` so the
        # narrow-and-walk stays expressive without lighting up the strict
        # check for every override-loader.
        node_dict: dict[Any, Any] = node
        node = node_dict.get(key)
    if not isinstance(node, list):
        return None

    items: list[Any] = node
    out: list[Dimension] = []
    for item in items:
        if not isinstance(item, dict):
            _LOGGER.warning(
                "judge dimension entry in %s is not a mapping; falling back",
                path,
            )
            return None
        try:
            out.append(Dimension.model_validate(item))
        except Exception:
            _LOGGER.warning(
                "judge dimension entry in %s failed validation; falling back",
                path,
            )
            return None
    if not out:
        return None
    return out


__all__ = [
    "Dimension",
    "JudgeScore",
    "load_dimensions",
    "persist_judge_scores",
    "score_outputs",
]
