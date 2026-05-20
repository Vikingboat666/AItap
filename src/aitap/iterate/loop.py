"""Self-iteration loop orchestrator (Wave 4 — full implementation).

This module replaces the Wave 3 ``iterate_one_round`` stub with the full
critique-and-revise loop described in
``docs/wave-4-design.md`` — judge + critic + impact + iterations DAO are
tied together here. Every call to :func:`iterate_loop` walks one
``session`` of an /iterate invocation:

1. Run the *baseline* round (the current head version against the
   dataset) and score it. No critic is invoked at this point — the
   baseline is the reference point all subsequent deltas measure
   against (Decision 3 of the design doc).
2. For each subsequent round:
   - Aggregate per-case judge scores + user thumbs/notes into an
     :class:`AggregatedFeedback`.
   - Dispatch to :func:`aitap.iterate.critic.revise` under one of three
     modes (``auto`` / ``guided`` / ``manual``).
   - Atomically: write the new ``prompt_versions`` row, run the new
     prompt through the dispatch adapter, score it with the judge, and
     persist the per-case scores + iteration row inside a single
     :func:`store.db.transaction` so a crash never leaves a half-state.
   - Check the convergence rules (Decision 3).
3. After the loop terminates, if the iterated prompt is part of a
   :class:`Pipeline`, persist the analyzed downstream impact on the last
   iteration row so the API/UI can surface the "N unverified consumers"
   banner.

Module-level contract
---------------------
- We **only** talk to LLMs through :class:`aitap.deep.client.LLMClient`.
  No direct provider SDK imports — tests pin this via the
  ``test_loop_module_does_not_import_provider_sdks`` assertion.
- We **never** call an LLM inside a SQLite write transaction. The judge,
  critic, and runner all run *before* the per-round
  ``transaction(immediate=True)``; the transaction only writes the new
  ``prompt_versions`` row + ``iterations`` row.
- ``CriticError`` aborts the loop with a failed-sentinel iteration row
  (``revise_mode="failed"`` + ``converged_reason="critic_failed"``). We
  do NOT retry — same broken prompt would produce the same broken reply
  and we'd loop forever; surfacing the failure lets the API/UI fall back
  to manual mode.

Convergence priority
--------------------
When multiple stop conditions fire on the same round, we report the
"good outcome" first so users see *why* iteration succeeded rather than
just that it timed out:

1. ``delta``      — score rose by ``delta_from_baseline`` (good)
2. ``absolute``   — opt-in non-negotiable axis cleared (good)
3. ``stagnation`` — no meaningful round-over-round movement (plateau)
4. ``max_rounds`` — ran out of rounds without converging (timeout)

``critic_failed`` is reported separately because it is a hard error
state, not a convergence outcome — the loop stopped because the rewriter
broke, not because the scoring decided it was done.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from aitap.iterate.critic import (
    AggregatedFeedback,
    CriticError,
    RevisedPrompt,
    ReviseMode,
    revise,
)
from aitap.iterate.impact import (
    DownstreamNode,
    analyze,
    serialize_status_for_iterations,
)
from aitap.iterate.judge import (
    load_dimensions,
    persist_judge_scores,
    score_outputs,
)
from aitap.iterate.judge_models import Dimension, JudgeScore
from aitap.playground import dispatch
from aitap.playground.runner import PromptRunResult, run_prompt
from aitap.scanner.models import Message, Pipeline, PromptSite
from aitap.server.routes import DatasetCase, RunOutput
from aitap.store import db as store_db
from aitap.store import files as store_files
from aitap.store import runs as runs_dao
from aitap.store.db import transaction
from aitap.store.history import record_version
from aitap.store.iterations import (
    Iteration,
    insert_iteration,
    new_session_id,
    read_iteration,
    serialize_downstream_status,
)

if TYPE_CHECKING:
    from aitap.config import Settings
    from aitap.deep.client import LLMClient

__all__ = [
    "ConvergenceConfig",
    "IterationOutcome",
    "check_convergence",
    "iterate_loop",
]


_LOGGER = logging.getLogger(__name__)

ConvergedReason = Literal["max_rounds", "delta", "stagnation", "absolute", "critic_failed"]

# Sentinel used on the iterations row when the critic LLM fails. The
# iterations table TEXT column has no CHECK constraint so we are free to
# write a non-enum value, but we keep it Python-typed as a string so a
# typo at the call site surfaces immediately.
_FAILED_REVISE_MODE = "failed"


# --------------------------------------------------------------------------- #
# Public models                                                               #
# --------------------------------------------------------------------------- #


class ConvergenceConfig(BaseModel):
    """Stop conditions for :func:`iterate_loop` — Decision 3 defaults.

    All three primary rules are **relative** (delta-from-baseline,
    round-over-round stagnation, hard cap on rounds). ``absolute_threshold``
    exists as the one legitimate use of an absolute score gate (e.g.
    safety must reach 0.95 regardless of baseline) but is ``None`` by
    default so judge-prompt drift and task heterogeneity don't make stops
    fragile.
    """

    model_config = ConfigDict(frozen=True)

    max_rounds: int = Field(default=5, ge=1)
    delta_from_baseline: float = Field(default=0.15, ge=0.0)
    stagnation_window: int = Field(default=3, ge=2)
    stagnation_epsilon: float = Field(default=0.02, ge=0.0)
    absolute_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class IterationOutcome(BaseModel):
    """Result of one /iterate session.

    ``iterations`` is the full list of persisted rows (baseline first,
    then revise rounds in chronological order). ``converged_reason`` is
    ``None`` only when the loop was forced to give up with no signal at
    all (should not happen in practice — ``max_rounds`` is the default
    catch-all). ``final_version`` is the prompt_versions.version of the
    most recently committed prompt body; for sessions that produced no
    revise rounds this equals the baseline parent_version (the head
    version at session start).
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    iterations: list[Iteration]
    converged_reason: ConvergedReason | None
    final_version: int


# --------------------------------------------------------------------------- #
# Convergence helper                                                          #
# --------------------------------------------------------------------------- #


def check_convergence(
    iterations: list[Iteration],
    config: ConvergenceConfig,
) -> ConvergedReason | None:
    """Decide whether the loop should stop after the latest round.

    Returns the reason name when any rule fires, ``None`` otherwise.
    Multi-rule priority (see module docstring): ``delta`` > ``absolute`` >
    ``stagnation`` > ``max_rounds``. We prefer "good outcome" reasons so
    users see why the loop succeeded, not just that it timed out.

    The single-row case (only the baseline has been written) never trips
    any rule — relative metrics need at least one revise round to compute
    a delta, and max_rounds is checked against the total round count
    (baseline + revise) where the cap is the number of *revise* rounds.
    """
    if not iterations:
        return None

    baseline = iterations[0]
    latest = iterations[-1]

    # Baseline-only: not enough data to evaluate any rule.
    if len(iterations) < 2:
        return None

    # 1. Delta-from-baseline — preferred "good outcome".
    delta = latest.weighted_score - baseline.weighted_score
    if delta >= config.delta_from_baseline:
        return "delta"

    # 2. Absolute threshold (opt-in, e.g. safety floors).
    if config.absolute_threshold is not None and latest.weighted_score >= config.absolute_threshold:
        return "absolute"

    # 3. Stagnation — needs ``stagnation_window`` consecutive rounds with
    #    round-over-round delta below epsilon. We measure over the most
    #    recent ``window`` rounds: that's window-1 pairwise deltas, all of
    #    which must be below epsilon to count as a plateau.
    if len(iterations) >= config.stagnation_window:
        window = iterations[-config.stagnation_window :]
        deltas = [
            abs(window[i].weighted_score - window[i - 1].weighted_score)
            for i in range(1, len(window))
        ]
        if deltas and all(d < config.stagnation_epsilon for d in deltas):
            return "stagnation"

    # 4. Max rounds — hard cap. ``len(iterations)`` already includes the
    #    baseline as round 1, so a max_rounds=5 cap fires at 5 total rows
    #    (1 baseline + 4 revise rounds). The user expectation in the design
    #    doc is "max_rounds limits how many revise rounds the loop tries",
    #    so we trip when total rows reach the cap.
    if len(iterations) >= config.max_rounds:
        return "max_rounds"

    return None


# --------------------------------------------------------------------------- #
# Public orchestrator                                                         #
# --------------------------------------------------------------------------- #


async def iterate_loop(
    *,
    settings: Settings,
    prompt_id: str,
    dataset_id: str,
    client: LLMClient,
    judge_client: LLMClient | None = None,
    critic_client: LLMClient | None = None,
    mode: ReviseMode = "auto",
    instruction: str | None = None,
    manual_revisions: dict[int, str] | None = None,
    user_thumbs: dict[int, dict[int, Literal["up", "down"]]] | None = None,
    user_notes: dict[int, dict[int, str]] | None = None,
    convergence: ConvergenceConfig | None = None,
    dimensions_override: list[Dimension] | None = None,
) -> IterationOutcome:
    """Run the full critique-and-revise loop for *prompt_id*.

    Parameters
    ----------
    settings:
        Project-rooted :class:`Settings`; we read the SQLite DB and the
        dataset sidecar through it.
    prompt_id:
        The :class:`PromptSite.id` to iterate on. Must exist in the
        ``prompts`` table.
    dataset_id:
        The dataset to evaluate every round against. Read from
        ``.aitap/datasets/<dataset_id>.cases.jsonl`` via the dispatch
        adapter — see :func:`aitap.playground.dispatch._resolve_cases`.
    client:
        Runner :class:`LLMClient` — used by the dispatch adapter to
        execute the prompt against each case. Tests pass a
        :class:`MockLLMClient`.
    judge_client:
        Optional separate :class:`LLMClient` for the judge call. When
        ``None`` we fall back to *client*. Splitting them is supported
        because the design contemplates a stronger judge model than the
        runner.
    critic_client:
        Optional separate :class:`LLMClient` for the rewriter. ``None``
        falls back to *client*. Manual mode never invokes it.
    mode:
        ``"auto"`` / ``"guided"`` / ``"manual"`` — same semantics as
        :func:`aitap.iterate.critic.revise`.
    instruction:
        Required for ``guided`` mode; otherwise ignored.
    manual_revisions:
        Mapping ``round -> new_template_text``. Required for ``manual``
        mode. Missing entries for a round abort the loop because the user
        promised to provide the text and didn't.
    user_thumbs:
        Optional ``{round -> {case_index -> "up" | "down"}}`` map. The
        round key is the round the thumbs *target* (i.e. the previous
        round's outputs that the user reacted to). Plumbed into the
        critic for the *following* round's :class:`AggregatedFeedback`.
    user_notes:
        Same shape as ``user_thumbs`` but free-text notes.
    convergence:
        Stop-condition config; ``None`` uses :class:`ConvergenceConfig`'s
        Wave-4 defaults.
    dimensions_override:
        Optional explicit dimension list; ``None`` runs
        :func:`load_dimensions` against the three-layer override stack.

    Returns
    -------
    :class:`IterationOutcome` summarising the session.
    """
    if user_thumbs is None:
        user_thumbs = {}
    if user_notes is None:
        user_notes = {}
    if manual_revisions is None:
        manual_revisions = {}

    convergence_cfg = convergence or ConvergenceConfig()
    judge_llm: LLMClient = judge_client or client
    critic_llm: LLMClient = critic_client or client

    session_id = new_session_id()

    # Load grounding once per session — the prompt's identity does not
    # change across rounds, only its template body.
    site = _load_prompt_site(settings, prompt_id)
    dimensions = (
        dimensions_override
        if dimensions_override is not None
        else load_dimensions(settings, prompt_id)
    )
    pipeline = _find_pipeline_containing(settings, prompt_id)

    # Resolve cases once per session — same dataset across every round.
    cases = _load_cases_from_dataset(settings, dataset_id)

    iterations: list[Iteration] = []

    # ---------------- Baseline round ----------------
    baseline_version = _latest_prompt_version(settings, prompt_id)
    if baseline_version < 1:
        raise ValueError(f"prompt {prompt_id!r} has no prompt_versions row yet; cannot iterate")

    baseline_result = await _run_round(site=site, cases=cases, client=client)
    baseline_outputs = _runner_outputs_to_judge_dicts(baseline_result)
    baseline_scores = await score_outputs(
        site_purpose=site.purpose or site.name,
        outputs=baseline_outputs,
        dimensions=dimensions,
        client=judge_llm,
    )
    baseline_iter = _persist_round(
        settings=settings,
        prompt_id=prompt_id,
        session_id=session_id,
        round_=1,
        is_baseline=True,
        parent_version=None,
        new_version=None,
        revise_mode=None,
        revise_instruction=None,
        critique_text=None,
        judge_scores=baseline_scores,
        dimensions=dimensions,
        downstream_status=None,
        converged_reason=None,
        round_outputs=baseline_result.outputs,
        round_responses=baseline_result.responses,
        site=site,
        baseline_version_for_run=baseline_version,
        dataset_id=dataset_id,
        new_version_payload=None,
    )
    iterations.append(baseline_iter)

    converged: ConvergedReason | None = check_convergence(iterations, convergence_cfg)
    final_version = baseline_version

    # ---------------- Revise rounds ----------------
    previous_scores = baseline_scores
    while converged is None:
        round_number = len(iterations) + 1

        feedback = _build_feedback(
            judge_scores=previous_scores,
            user_thumbs=user_thumbs.get(round_number - 1, {}),
            user_notes=user_notes.get(round_number - 1, {}),
        )

        # Resolve the parent template body the critic will see. We hand
        # the critic the verbatim prompt body the previous round used.
        parent_version_for_round = final_version
        parent_template_text = _read_template_text(settings, prompt_id, parent_version_for_round)

        # Critic call — outside any DB transaction.
        try:
            revised = await _do_revise(
                site=site,
                parent_template_text=parent_template_text,
                feedback=feedback,
                mode=mode,
                instruction=instruction,
                manual_text=manual_revisions.get(round_number),
                client=critic_llm,
                dimensions=dimensions,
            )
        except CriticError as exc:
            _LOGGER.warning("critic failed in round %d: %s", round_number, exc)
            failed_iter = _persist_failed_round(
                settings=settings,
                prompt_id=prompt_id,
                session_id=session_id,
                round_=round_number,
                parent_version=parent_version_for_round,
                instruction=instruction if mode == "guided" else None,
                critique_text=str(exc),
            )
            iterations.append(failed_iter)
            converged = "critic_failed"
            break

        # Build the revised PromptSite in memory and run it. No DB write
        # happens until we know the round succeeded.
        round_messages = _compose_revised_messages(site, revised.template_text)
        revised_site = site.model_copy(update={"messages": round_messages})
        round_result = await _run_round(
            site=revised_site,
            cases=cases,
            client=client,
        )
        new_outputs = _runner_outputs_to_judge_dicts(round_result)
        new_scores = await score_outputs(
            site_purpose=site.purpose or site.name,
            outputs=new_outputs,
            dimensions=dimensions,
            client=judge_llm,
        )

        critique_text = _summarise_critiques(new_scores)

        # One atomic transaction commits the new version + iteration row
        # + runs row + scores. All LLM I/O already happened above; this
        # block touches only SQLite.
        new_version_payload = _NewVersionPayload(
            messages=round_messages,
            note=_truncate(revised.rationale, 200),
        )
        round_iter, new_version = _persist_round_with_new_version(
            settings=settings,
            prompt_id=prompt_id,
            session_id=session_id,
            round_=round_number,
            parent_version=parent_version_for_round,
            new_version_payload=new_version_payload,
            site=site,
            revise_mode=revised.mode,
            revise_instruction=revised.instruction,
            critique_text=critique_text,
            judge_scores=new_scores,
            dimensions=dimensions,
            round_outputs=round_result.outputs,
            round_responses=round_result.responses,
            dataset_id=dataset_id,
        )
        iterations.append(round_iter)
        previous_scores = new_scores
        final_version = new_version

        converged = check_convergence(iterations, convergence_cfg)

    # ---------------- Impact analysis (post-loop) ----------------
    downstream_nodes: list[DownstreamNode] = []
    if pipeline is not None:
        downstream_nodes = analyze(pipeline, prompt_id)

    # Stamp the converged_reason + downstream_status on the final row so
    # the API surface has the canonical "loop completed" state in a
    # single DB row.
    if iterations:
        last = iterations[-1]
        downstream_status: dict[str, str] | None
        if pipeline is not None:
            downstream_status = serialize_status_for_iterations(downstream_nodes)
        else:
            downstream_status = None
        updated = _update_final_iteration(
            settings=settings,
            iteration=last,
            converged_reason=converged,
            downstream_status=downstream_status,
        )
        iterations[-1] = updated

    return IterationOutcome(
        session_id=session_id,
        iterations=iterations,
        converged_reason=converged,
        final_version=final_version,
    )


# --------------------------------------------------------------------------- #
# Round helpers                                                               #
# --------------------------------------------------------------------------- #


async def _run_round(
    *,
    site: PromptSite,
    cases: list[DatasetCase],
    client: LLMClient,
) -> PromptRunResult:
    """Execute *site* against *cases* using the supplied LLM client.

    We call :func:`run_prompt` directly (not the higher-level
    :func:`aitap.playground.dispatch.invoke_run` adapter) so the orchestrator
    keeps full control of when DB writes happen. The dispatch adapter is
    designed around the HTTP request lifecycle — it inserts the ``runs``
    row, runs the prompt, then updates the row. For our purposes that
    interleaves a write before we know the round succeeded, which is
    exactly what we want to avoid for atomicity.

    Returns the runner's bundle directly; the caller is responsible for
    converting outputs into the judge-friendly dict shape and for the
    eventual ``runs`` + ``scores`` table writes inside the per-round
    transaction.
    """
    return await run_prompt(
        site=site,
        version=0,  # unused by run_prompt; persisted by the loop's own DB writes
        dataset_cases=cases,
        client=client,
        parameters=site.parameters,
    )


def _runner_outputs_to_judge_dicts(result: PromptRunResult) -> list[dict[str, Any]]:
    """Pair RunOutputs with their per-case responses into judge-input dicts.

    Mirrors the format :func:`aitap.playground.dispatch._run_output_to_record`
    produces in the JSONL sidecar — the judge module already knows this
    shape (text + intermediate + error + cost_usd + usage + latency_ms),
    so we keep the orchestrator's in-memory path bit-identical.
    """
    out: list[dict[str, Any]] = []
    responses = result.responses or [None] * len(result.outputs)
    for output, response in zip(result.outputs, responses, strict=False):
        record: dict[str, Any] = {
            "case_index": output.case_index,
            "text": output.text,
            "image_path": output.image_path,
            "error": output.error,
            "intermediate": output.intermediate,
            "cost_usd": response.cost_usd if response is not None else None,
            "usage": (
                {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                if response is not None
                else None
            ),
            "latency_ms": None,
        }
        out.append(record)
    return out


def _load_cases_from_dataset(settings: Settings, dataset_id: str) -> list[DatasetCase]:
    """Resolve the dataset id into a list of :class:`DatasetCase`.

    Matches :func:`aitap.playground.dispatch._resolve_cases`' fallback
    path — the loop only ever uses dataset-driven runs (per the M4
    contract; the user re-iterating with inline cases is an API-layer
    concern). Missing or empty dataset means zero cases, which the
    judge tolerates.
    """
    path = store_files.dataset_path(settings.datasets_dir, dataset_id)
    if not path.exists():
        return []
    rows = store_files.read_cases(path)
    return [DatasetCase(inputs=row) for row in rows]


async def _do_revise(
    *,
    site: PromptSite,
    parent_template_text: str,
    feedback: AggregatedFeedback,
    mode: ReviseMode,
    instruction: str | None,
    manual_text: str | None,
    client: LLMClient,
    dimensions: list[Dimension],
) -> RevisedPrompt:
    """Dispatch :func:`aitap.iterate.critic.revise` with the resolved args.

    Manual mode without a corresponding ``manual_revisions[round]``
    surfaces as a ``ValueError`` from the critic. Guided mode without an
    instruction same. Both are caller bugs — we let them propagate so
    the API layer can map to a 400.
    """
    if mode == "manual" and manual_text is None:
        raise ValueError("manual mode requires a manual_revisions entry for this round")
    return await revise(
        prompt=site,
        current_template=parent_template_text,
        feedback=feedback,
        mode=mode,
        client=client,
        instruction=instruction,
        manual_text=manual_text,
        dimensions=dimensions,
    )


def _build_feedback(
    *,
    judge_scores: list[JudgeScore],
    user_thumbs: dict[int, Literal["up", "down"]],
    user_notes: dict[int, str],
) -> AggregatedFeedback:
    """Pack the round's signals into the critic's input contract."""
    return AggregatedFeedback(
        judge_scores=judge_scores,
        user_thumbs=user_thumbs,
        user_notes=user_notes,
    )


# --------------------------------------------------------------------------- #
# Persistence helpers                                                         #
# --------------------------------------------------------------------------- #


class _NewVersionPayload(BaseModel):
    """Carrier for "we want to mint a new prompt_versions row this round."

    Used by :func:`_persist_round_with_new_version` so the version write
    and the iteration write live inside one ``transaction(immediate=True)``
    — without this carrier we'd have to pass eight extra kwargs through
    a signature that already has too many.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    messages: list[Message]
    note: str | None


def _persist_round(
    *,
    settings: Settings,
    prompt_id: str,
    session_id: str,
    round_: int,
    is_baseline: bool,
    parent_version: int | None,
    new_version: int | None,
    revise_mode: ReviseMode | None,
    revise_instruction: str | None,
    critique_text: str | None,
    judge_scores: list[JudgeScore],
    dimensions: list[Dimension],
    downstream_status: dict[str, str] | None,
    converged_reason: ConvergedReason | None,
    round_outputs: list[RunOutput],
    round_responses: list[Any],
    site: PromptSite,
    baseline_version_for_run: int,
    dataset_id: str,
    new_version_payload: _NewVersionPayload | None,
) -> Iteration:
    """Atomically persist the baseline round (no new prompt_version).

    Used for round 1. Steps inside one ``transaction(immediate=True)``:

    1. Insert a ``runs`` row + per-case scores rows so the
       ``persist_judge_scores`` FK is satisfied.
    2. Insert the ``iteration`` row with ``is_baseline=True``,
       ``new_version=None``.

    The outputs sidecar is written *before* the transaction (it lives on
    disk and is idempotent through atomic rename) so the failure mode is
    bounded: a crash after the sidecar write but before the transaction
    leaves an orphan sidecar, never a partial DB state.
    """
    del new_version_payload  # baseline path never mints a new version

    weighted = _aggregate_round_score(judge_scores)
    per_dim = _aggregate_per_dim(judge_scores, dimensions)
    started = datetime.now(timezone.utc)

    run_id = runs_dao.new_run_id(prompt_id, baseline_version_for_run)

    # Write the sidecar outside the transaction — it's a filesystem op,
    # not a SQLite write, and we want the DB transaction to be tight.
    _write_outputs_sidecar(
        settings=settings,
        run_id=run_id,
        outputs=round_outputs,
        responses=round_responses,
    )

    conn = _open_conn(settings)
    try:
        with transaction(conn, immediate=True):
            runs_dao.insert_run(
                conn,
                run_id=run_id,
                target_kind="prompt",
                target_id=prompt_id,
                target_version=baseline_version_for_run,
                dataset_id=dataset_id,
                provider=site.provider.value,
                model=site.parameters.model or "model-unknown",
                parameters_json=runs_dao.serialize_parameters(site.parameters),
                status="done",
                cost_usd=_aggregate_round_cost(round_responses),
            )
            runs_dao.update_run_status(
                conn,
                run_id,
                status="done",
                cost_usd=_aggregate_round_cost(round_responses),
                finished=True,
            )
            persist_judge_scores(
                conn,
                run_id=run_id,
                scores=judge_scores,
                judge_name="llm-judge",
            )
            iter_id = insert_iteration(
                conn,
                prompt_id=prompt_id,
                session_id=session_id,
                round=round_,
                is_baseline=is_baseline,
                parent_version=parent_version,
                new_version=new_version,
                revise_mode=revise_mode,
                revise_instruction=revise_instruction,
                critique_text=critique_text,
                weighted_score=weighted,
                per_dim_scores=per_dim,
                downstream_status=downstream_status,
                converged_reason=cast("Any", converged_reason),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        persisted = read_iteration(conn, iter_id)
        if persisted is None:  # pragma: no cover — defensive
            raise RuntimeError(f"failed to re-read iteration row {iter_id!r}")
        return persisted
    finally:
        conn.close()


def _persist_round_with_new_version(
    *,
    settings: Settings,
    prompt_id: str,
    session_id: str,
    round_: int,
    parent_version: int,
    new_version_payload: _NewVersionPayload,
    site: PromptSite,
    revise_mode: ReviseMode,
    revise_instruction: str | None,
    critique_text: str | None,
    judge_scores: list[JudgeScore],
    dimensions: list[Dimension],
    round_outputs: list[RunOutput],
    round_responses: list[Any],
    dataset_id: str,
) -> tuple[Iteration, int]:
    """Atomically commit version + run + scores + iteration for one revise round.

    The full atomic unit (Decision 5 of the design doc — wt/loop owns
    the "one atomic write per round" semantics):

    1. ``record_version`` — mint the new ``prompt_versions`` row.
    2. ``insert_run`` + ``update_run_status`` — persist the runs row
       that ``persist_judge_scores`` FK's against.
    3. ``persist_judge_scores`` — one score row per case.
    4. ``insert_iteration`` — the canonical event-log row keyed on
       ``(prompt_id, session_id, round)``.

    All four happen inside a single ``transaction(immediate=True)``; if
    *any* fails the round rolls back to "no new version, no iteration
    row, no score rows" — the previous baseline / prior round is left
    untouched.

    Returns the (re-read Iteration, new_version) tuple so the caller
    can store the version for the next round's parent.
    """
    weighted = _aggregate_round_score(judge_scores)
    per_dim = _aggregate_per_dim(judge_scores, dimensions)
    started = datetime.now(timezone.utc)
    cost = _aggregate_round_cost(round_responses)

    conn = _open_conn(settings)
    try:
        with transaction(conn, immediate=True):
            new_version = record_version(
                conn,
                prompt_id,
                messages=new_version_payload.messages,
                parameters=site.parameters,
                note=new_version_payload.note,
                created_by="iteration",
                parent_version=parent_version,
            )
            run_id = runs_dao.new_run_id(prompt_id, new_version)
            runs_dao.insert_run(
                conn,
                run_id=run_id,
                target_kind="prompt",
                target_id=prompt_id,
                target_version=new_version,
                dataset_id=dataset_id,
                provider=site.provider.value,
                model=site.parameters.model or "model-unknown",
                parameters_json=runs_dao.serialize_parameters(site.parameters),
                status="done",
                cost_usd=cost,
            )
            runs_dao.update_run_status(
                conn,
                run_id,
                status="done",
                cost_usd=cost,
                finished=True,
            )
            persist_judge_scores(
                conn,
                run_id=run_id,
                scores=judge_scores,
                judge_name="llm-judge",
            )
            iter_id = insert_iteration(
                conn,
                prompt_id=prompt_id,
                session_id=session_id,
                round=round_,
                is_baseline=False,
                parent_version=parent_version,
                new_version=new_version,
                revise_mode=revise_mode,
                revise_instruction=revise_instruction,
                critique_text=critique_text,
                weighted_score=weighted,
                per_dim_scores=per_dim,
                downstream_status=None,
                converged_reason=None,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        # Sidecar write after commit — if it fails the DB state still
        # reflects the round (the next round's parent_version is valid).
        # Sidecar absence is the documented "no per-case outputs"
        # outcome for the reader.
        _write_outputs_sidecar(
            settings=settings,
            run_id=run_id,
            outputs=round_outputs,
            responses=round_responses,
        )
        persisted = read_iteration(conn, iter_id)
        if persisted is None:  # pragma: no cover
            raise RuntimeError(f"failed to re-read iteration row {iter_id!r}")
        return persisted, new_version
    finally:
        conn.close()


def _aggregate_round_cost(responses: list[Any]) -> float:
    """Sum cost_usd across per-case ChatResponses; None entries count as 0.

    We accept ``list[Any]`` because the runner's typed
    ``list[ChatResponse | None]`` doesn't survive being threaded through
    ``_persist_round`` without an explicit ``ChatResponse`` import here
    (which would create a wider deep package coupling). The runner only
    ever emits ``ChatResponse`` or ``None`` so the duck-typed attribute
    access is safe.
    """
    total = 0.0
    for r in responses:
        if r is not None and hasattr(r, "cost_usd"):
            total += float(r.cost_usd)
    return total


def _write_outputs_sidecar(
    *,
    settings: Settings,
    run_id: str,
    outputs: list[RunOutput],
    responses: list[Any],
) -> None:
    """Reuse the dispatch adapter's atomic sidecar writer.

    The dispatch module owns the canonical sidecar layout + atomic
    rename; we route through its public helper so the JSON shape stays
    a single source of truth. If the writer's signature changes one day,
    the orchestrator follows automatically.
    """
    # The dispatch helper expects ``list[ChatResponse | None] | None``;
    # we already enforce that at the caller (run_prompt.responses is
    # exactly that type) so a runtime cast is the cheapest way to land
    # the call without re-importing ChatResponse here.
    dispatch.write_outputs_sidecar(
        settings=settings,
        run_id=run_id,
        outputs=outputs,
        responses=cast("Any", responses),
    )


def _persist_failed_round(
    *,
    settings: Settings,
    prompt_id: str,
    session_id: str,
    round_: int,
    parent_version: int,
    instruction: str | None,
    critique_text: str,
) -> Iteration:
    """Write a sentinel iteration row when the critic LLM failed.

    The row exists so the API/UI surface can distinguish "the loop is
    still running" from "the critic broke". ``revise_mode`` is the
    sentinel string ``"failed"``; ``new_version`` is NULL because no new
    prompt body was committed. ``weighted_score`` is set to 0.0 — there
    are no per-case scores from a failed round (the judge ran on the
    *baseline* / previous round's output, not this round's) and 0.0 keeps
    convergence math from accidentally counting this as an improvement.
    """
    started = datetime.now(timezone.utc)
    conn = _open_conn(settings)
    try:
        with transaction(conn, immediate=True):
            iter_id = insert_iteration(
                conn,
                prompt_id=prompt_id,
                session_id=session_id,
                round=round_,
                is_baseline=False,
                parent_version=parent_version,
                new_version=None,
                revise_mode=cast("Any", _FAILED_REVISE_MODE),
                revise_instruction=instruction,
                critique_text=critique_text,
                weighted_score=0.0,
                per_dim_scores={},
                downstream_status=None,
                converged_reason=cast("Any", "critic_failed"),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        persisted = read_iteration(conn, iter_id)
        if persisted is None:  # pragma: no cover
            raise RuntimeError(f"failed to re-read failed iteration row {iter_id!r}")
        return persisted
    finally:
        conn.close()


def _update_final_iteration(
    *,
    settings: Settings,
    iteration: Iteration,
    converged_reason: ConvergedReason | None,
    downstream_status: dict[str, str] | None,
) -> Iteration:
    """Stamp converged_reason + downstream_status on the last iteration row.

    The convergence + impact information isn't known when the row is
    first inserted (we only learn whether the loop is done after
    persisting the round and re-checking ``check_convergence``). A small
    UPDATE here keeps the persisted row's state authoritative without
    re-inserting.
    """
    conn = _open_conn(settings)
    try:
        with transaction(conn, immediate=True):
            conn.execute(
                """
                UPDATE iterations
                SET converged_reason = ?, downstream_status = ?
                WHERE id = ?
                """,
                (
                    converged_reason,
                    serialize_downstream_status(downstream_status),
                    iteration.id,
                ),
            )
        persisted = read_iteration(conn, iteration.id)
        if persisted is None:  # pragma: no cover
            raise RuntimeError(f"failed to re-read iteration {iteration.id!r} after final update")
        return persisted
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Aggregation helpers                                                         #
# --------------------------------------------------------------------------- #


def _aggregate_round_score(scores: list[JudgeScore]) -> float:
    """Mean ``weighted_total`` across all cases in the round.

    Empty input degrades to 0.0 — the convergence detector treats it
    as "no signal", which is the same outcome as a fully-failed judge
    batch (every case zero). We log this elsewhere; the math here just
    keeps the return type a clean ``float``.
    """
    if not scores:
        return 0.0
    return sum(s.weighted_total for s in scores) / len(scores)


def _aggregate_per_dim(
    scores: list[JudgeScore],
    dimensions: list[Dimension],
) -> dict[str, float]:
    """Mean per-dim score across cases — one entry per active dimension.

    We key the result by ``Dimension.name`` (rather than by whichever
    keys the judge actually returned) so the persisted JSON shape stays
    stable even when the judge omits a dim. Missing dims default to 0.0.
    """
    if not scores:
        return {dim.name: 0.0 for dim in dimensions}
    out: dict[str, float] = {}
    for dim in dimensions:
        values: list[float] = []
        for s in scores:
            v = s.per_dim.get(dim.name)
            if v is not None:
                values.append(v)
        out[dim.name] = sum(values) / len(values) if values else 0.0
    return out


def _summarise_critiques(scores: list[JudgeScore]) -> str | None:
    """Pick a representative critique text to persist on the iteration row.

    We surface the worst-scoring case's critique (the one most likely to
    explain why the round didn't converge). Returns ``None`` when no
    case has a non-empty critique so the column stays NULL rather than
    storing an empty string.
    """
    with_text = [s for s in scores if s.critique]
    if not with_text:
        return None
    worst = min(with_text, key=lambda s: s.weighted_total)
    return _truncate(worst.critique, 400)


def _truncate(text: str, limit: int) -> str:
    """Cap text at *limit* characters with an ellipsis marker.

    Used for the rationale on the prompt_versions note column (which is
    not strictly bounded by schema but doesn't need to be MB-sized) and
    for the critique_text on the iteration row.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Filesystem / DB plumbing                                                    #
# --------------------------------------------------------------------------- #


def _open_conn(settings: Settings) -> sqlite3.Connection:
    """Open + init a fresh connection.

    Kept as a private helper so the loop never accidentally re-uses a
    connection across the boundary between LLM I/O (potentially seconds
    of network wait) and a write transaction. A long-held connection
    would hold the connection-level lock during the LLM call.
    """
    conn = store_db.connect(settings.db_path)
    store_db.init_db(conn)
    return conn


def _load_prompt_site(settings: Settings, prompt_id: str) -> PromptSite:
    """Read the canonical :class:`PromptSite` payload from the store.

    Mirrors :func:`aitap.playground.dispatch._load_prompt_site` so any
    future contract changes (e.g. PromptSite gains a field) flow through
    one place without re-thinking JSON shape per call site.
    """
    conn = _open_conn(settings)
    try:
        cur = conn.execute("SELECT payload_json FROM prompts WHERE id = ?", (prompt_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"prompt {prompt_id!r} not found in store")
        return PromptSite.model_validate_json(str(row["payload_json"]))
    finally:
        conn.close()


def _latest_prompt_version(settings: Settings, prompt_id: str) -> int:
    """Return the highest ``prompt_versions.version`` for the prompt."""
    conn = _open_conn(settings)
    try:
        return runs_dao.latest_prompt_version(conn, prompt_id)
    finally:
        conn.close()


def _read_template_text(settings: Settings, prompt_id: str, version: int) -> str:
    """Return the plain-text body the critic should rewrite.

    We concatenate every message's ``template_text`` (separated by
    newlines) into one string so the critic sees the full prompt body
    regardless of how many messages compose it. The reverse — splitting
    the critic's rewrite back into messages — happens in
    :func:`_compose_revised_messages`.
    """
    conn = _open_conn(settings)
    try:
        cur = conn.execute(
            "SELECT template_json FROM prompt_versions WHERE prompt_id = ? AND version = ?",
            (prompt_id, version),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(
                f"prompt {prompt_id!r} has no version {version} — cannot resolve template"
            )
        payload = json.loads(str(row["template_json"]))
    finally:
        conn.close()

    if not isinstance(payload, list):
        return ""
    items: list[Any] = cast("list[Any]", payload)
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item_dict: dict[Any, Any] = item
            text = item_dict.get("template_text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _compose_revised_messages(site: PromptSite, new_text: str) -> list[Message]:
    """Stitch the critic's output back into a list-of-messages payload.

    Strategy: clone the original message stack, but replace the first
    *system* message (or the first message if there is no system one)
    with the critic's rewrite. Other messages are kept intact so any
    interpolation slots (``{user_input}`` etc.) survive across the
    iteration. This is the same shape the playground runner expects.
    """
    messages = list(site.messages)
    target_idx = next(
        (i for i, m in enumerate(messages) if m.role.value == "system"),
        0 if messages else None,
    )
    if target_idx is None:
        return [
            Message(
                role=site.messages[0].role if messages else _default_role(), template_text=new_text
            )
        ]
    original = messages[target_idx]
    messages[target_idx] = Message(
        role=original.role,
        template_text=new_text,
        template_kind=original.template_kind,
        variables=original.variables,
    )
    return messages


def _default_role():  # type: ignore[no-untyped-def]
    """Fallback role when the original prompt has zero messages.

    Edge case only — the scanner shouldn't produce a zero-message site
    in practice, but we still need a valid Role enum value to return so
    pydantic accepts the manufactured Message.
    """
    from aitap.scanner.models import Role as _Role

    return _Role.SYSTEM


def _find_pipeline_containing(settings: Settings, prompt_id: str) -> Pipeline | None:
    """Return the first pipeline whose node set includes *prompt_id*.

    We scan all pipeline rows in detection order; a prompt may appear in
    multiple pipelines but the impact analyzer's downstream walk is
    pipeline-scoped, so we pick the first one for the post-loop
    downstream-status stamp. The future API surface can let the user
    pick which pipeline they cared about.
    """
    conn = _open_conn(settings)
    try:
        rows = store_db.read_pipelines(conn)
        for row in rows:
            try:
                pipeline = Pipeline.model_validate_json(str(row["payload_json"]))
            except Exception:
                _LOGGER.warning("skipping malformed pipeline row %s", row["id"])
                continue
            if any(n.prompt_id == prompt_id for n in pipeline.nodes):
                return pipeline
        return None
    finally:
        conn.close()
