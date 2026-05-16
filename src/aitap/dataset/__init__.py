"""Test-case generators for prompts (the "L0/L1/L2 三层" in the plan).

The orchestrator :func:`generate_cases` picks one of four generation modes
and returns a list of :class:`Case` rows. Each sub-module owns one mode:

- ``"seed"`` — :mod:`aitap.dataset.seed` reads existing user seeds.
- ``"expand"`` — :mod:`aitap.dataset.llm_expander` asks an LLM to produce
  boundary/adversarial/noise variants from seeds + prompt purpose.
- ``"context"`` — :mod:`aitap.dataset.code_context` infers the input shape
  from the call site's enclosing function and feeds it to the expander.
- ``"fixtures"`` — :mod:`aitap.dataset.fixture_miner` lifts dict literals
  out of ``tests/``, ``fixtures/``, and ``examples/``.

Public API:

- :func:`generate_cases`
- :class:`Case`, :class:`InputShape`, :func:`case_id` (re-exported from
  :mod:`aitap.dataset.types`)
- Sub-module functions :func:`load_seeds`, :func:`save_seeds`,
  :func:`infer_input_shape`, :func:`find_candidate_inputs`,
  :func:`expand`
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aitap.dataset.code_context import infer_input_shape
from aitap.dataset.fixture_miner import find_candidate_inputs
from aitap.dataset.llm_expander import expand
from aitap.dataset.seed import load_seeds, normalize_seeds, save_seeds
from aitap.dataset.types import Case, InputShape, case_id

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite


GenerationMode = Literal["seed", "expand", "context", "fixtures"]
"""Which generator the orchestrator runs. See module docstring for details."""


async def generate_cases(
    site: PromptSite,
    mode: GenerationMode = "expand",
    n: int = 10,
    client: LLMClient | None = None,
    *,
    project_root: Path | None = None,
    seeds: Iterable[Case | Mapping[str, Any]] | None = None,
    datasets_dir: Path | None = None,
    dataset_name: str | None = None,
) -> list[Case]:
    """Generate up to *n* test cases for *site* using the requested *mode*.

    The orchestrator deliberately accepts every input every mode could
    possibly need; modes ignore what they don't use. This keeps the
    callsite (CLI / server / UI) ergonomic — one function, one signature.

    Mode contracts
    --------------
    ``"seed"``
        Returns up to *n* cases currently in
        ``datasets_dir/<dataset_name>.cases.jsonl``. Requires
        ``datasets_dir``; ``dataset_name`` defaults to ``site.name`` to
        match the convention used by the dataset editor UI.

    ``"expand"``
        Asks the LLM (``client`` required) to expand *seeds* into *n*
        variants. ``seeds`` defaults to whatever's already on disk for the
        site if ``datasets_dir`` is supplied. ``site.purpose`` is fed in
        as grounding.

    ``"context"``
        Like ``"expand"``, but additionally feeds the
        :class:`InputShape` inferred from *project_root* as extra
        grounding. Requires ``project_root``. Falls back to plain
        ``"expand"`` behaviour if no shape could be inferred (still
        useful — the prompt's purpose alone gives the LLM enough to go on).

    ``"fixtures"``
        No LLM call. Scans *project_root* for dict/JSON literals that
        look like prompt inputs and returns up to *n* of them.
    """
    if mode == "seed":
        return _run_seed_mode(site, n=n, datasets_dir=datasets_dir, dataset_name=dataset_name)

    if mode == "fixtures":
        if project_root is None:
            raise ValueError("mode='fixtures' requires project_root")
        return find_candidate_inputs(project_root, site, max_candidates=n)

    # The two LLM modes share most of the prep work.
    if client is None:
        raise ValueError(f"mode={mode!r} requires an LLMClient")

    effective_seeds = _resolve_seeds(
        site, seeds=seeds, datasets_dir=datasets_dir, dataset_name=dataset_name
    )
    if not effective_seeds:
        raise ValueError(
            f"mode={mode!r} requires at least one seed case "
            "(pass via seeds= or place in the dataset file first)"
        )

    shape: InputShape | None = None
    if mode == "context":
        if project_root is None:
            raise ValueError("mode='context' requires project_root")
        shape = infer_input_shape(site, project_root)

    return await expand(
        effective_seeds,
        count=n,
        client=client,
        prompt_purpose=site.purpose,
        prompt_site_id=site.id,
        input_shape=shape,
        source="context" if mode == "context" else "expand",
    )


def _run_seed_mode(
    site: PromptSite,
    *,
    n: int,
    datasets_dir: Path | None,
    dataset_name: str | None,
) -> list[Case]:
    if datasets_dir is None:
        raise ValueError("mode='seed' requires datasets_dir")
    name = dataset_name or site.name
    cases = load_seeds(datasets_dir, name)
    return cases[:n]


def _resolve_seeds(
    site: PromptSite,
    *,
    seeds: Iterable[Case | Mapping[str, Any]] | None,
    datasets_dir: Path | None,
    dataset_name: str | None,
) -> list[Case]:
    """Decide which seeds the expander should see.

    Order of preference:

    1. Explicit ``seeds=`` argument (caller knows best).
    2. Cases already saved under the site's dataset file.
    3. Empty list — caller will get a clear error from :func:`expand`.
    """
    if seeds is not None:
        return normalize_seeds(seeds, prompt_site_id=site.id)
    if datasets_dir is None:
        return []
    name = dataset_name or site.name
    return load_seeds(datasets_dir, name)


__all__ = [
    "Case",
    "GenerationMode",
    "InputShape",
    "case_id",
    "expand",
    "find_candidate_inputs",
    "generate_cases",
    "infer_input_shape",
    "load_seeds",
    "save_seeds",
]
