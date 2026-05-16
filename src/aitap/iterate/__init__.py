"""Self-iteration loop (Wave 3 stub).

The full critique-and-revise loop with an LLM-as-judge lands in M4. For now
this module exposes the *shape* of one iteration round so the ``POST
/api/runs/{id}/iterate`` endpoint can be wired end-to-end against the runs /
feedback tables.

What "one round" does today:

1. Read the latest version of the run's target prompt.
2. Read all feedback collected against the run.
3. Mint a new ``prompt_versions`` row with ``created_by='iteration'`` and a
   note summarising how many feedback items drove it. The template itself
   is copied verbatim from the parent version — the real LLM-driven rewrite
   is M4.
4. Return an ``IterationOutcome`` the route handler can turn into an
   ``IterateResponse``.

This stub deliberately does **not**:

- Call any LLM (no provider client is constructed).
- Compute convergence; ``converged=False`` always.
- Touch downstream pipeline nodes (impact-radius regression is M5+).

When M4 lands, ``iterate_one_round`` will gain a critic + judge dependency
and the body of step 3 becomes "rewrite the template with the critic, score
with the judge, decide whether to accept the new version."
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import cast

from aitap.store import runs as runs_dao


@dataclass(frozen=True)
class IterationOutcome:
    """Result of a single :func:`iterate_one_round` call.

    The fields are 1:1 with what the API layer needs to fill an
    :class:`aitap.server.routes.IterateResponse`, but kept as a plain
    dataclass so the iterate package has no FastAPI dependency.
    """

    new_version: int
    score_before: float | None
    score_after: float | None
    converged: bool
    downstream_impact: list[str]


def iterate_one_round(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    note_prefix: str = "iteration",
) -> IterationOutcome:
    """Promote the run's target prompt by one version, attributed to *iteration*.

    Raises :class:`ValueError` when:

    - ``run_id`` doesn't exist;
    - the run targets a pipeline (pipeline-level iteration is out of scope
      for Wave 3 — the endpoint should 400 rather than silently no-op).

    The new version's template is copied from the parent so this stub is a
    no-op semantically (apart from creating an audit trail) until M4 fills
    in the real rewrite.
    """
    run = runs_dao.read_run(conn, run_id)
    if run is None:
        raise ValueError(f"run {run_id!r} not found")

    target_kind = cast(str, run["target_kind"])
    if target_kind != "prompt":
        raise ValueError(
            f"iterate_one_round only supports target_kind='prompt' in Wave 3, got {target_kind!r}"
        )
    target_id = cast(str, run["target_id"])
    target_version = int(run["target_version"])

    # The score-before is whatever the run accumulated; in Wave 3 we don't
    # yet score outputs automatically, so this is typically None — we still
    # surface it via avg_score so callers wiring a rule judge see a value.
    score_before = runs_dao.avg_score(conn, run_id)

    feedback_rows = runs_dao.read_feedback(conn, run_id)

    # Fetch the parent version we're forking from. If the prompt was
    # registered without an explicit version row (the common case in tests
    # where the scanner has only inserted into ``prompts``), fall back to
    # the run's own target_version metadata.
    parent_row = _find_version_row(conn, target_id, target_version)
    if parent_row is not None:
        template_json = cast(str, parent_row["template_json"])
        parameters_json = cast(str, parent_row["parameters_json"])
    else:
        # No prior version row — seed with empty template / params so the
        # row still satisfies the NOT NULL constraint. Real wiring will
        # come once the Prompts API (wt/api-prompts) is creating
        # version 1 rows on first save.
        template_json = json.dumps([])
        parameters_json = json.dumps({})

    next_version = runs_dao.latest_prompt_version(conn, target_id) + 1
    note = f"{note_prefix} from run {run_id} ({len(feedback_rows)} feedback)"

    runs_dao.insert_prompt_version(
        conn,
        prompt_id=target_id,
        version=next_version,
        template_json=template_json,
        parameters_json=parameters_json,
        note=note,
        created_by="iteration",
        parent_version=target_version,
    )

    # M4 will replace this with the post-rewrite judge result; for now we
    # echo score_before so the response surface has the right shape.
    return IterationOutcome(
        new_version=next_version,
        score_before=score_before,
        score_after=score_before,
        converged=False,
        downstream_impact=[],
    )


def _find_version_row(
    conn: sqlite3.Connection,
    prompt_id: str,
    version: int,
) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT * FROM prompt_versions WHERE prompt_id = ? AND version = ?",
        (prompt_id, version),
    )
    return cur.fetchone()


__all__ = ["IterationOutcome", "iterate_one_round"]
