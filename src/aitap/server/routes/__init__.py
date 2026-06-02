"""HTTP API contract.

Contract version: 3 (2026-05-31) — breaking change: provider enum → profiles list.
The legacy provider-keyed request/response types (``ProviderKeyStatus``,
``SetKeyRequest``, ``TestKeyResponse``, ``SettingsUpdate``) and the
``SettingsResponse.keys`` field are removed in this version. The
multi-provider ``Profile`` / ``Defaults`` / ``ProfileUpsertRequest`` /
``ProfileTestResponse`` types added in v2 are now the only documented
key-management surface. The associated legacy routes
(``POST /api/settings/key``, ``DELETE /api/settings/key/{provider}``,
``POST /api/settings/test/{provider}``, ``PUT /api/settings``) are
removed alongside the types. See ``docs/profiles-design.md`` for the
redesign rationale and ``CONTRACTS.md`` for the change protocol.

Pydantic request/response models defining the surface of the FastAPI
backend that the React frontend consumes. After any change here,
regenerate TypeScript types:

    pnpm --dir src/aitap/ui run gen:api

Endpoint inventory (full implementation lives in sibling route modules):

    GET    /api/prompts                  -> PromptListResponse
    GET    /api/prompts/{prompt_id}      -> PromptDetailResponse
    POST   /api/prompts/{prompt_id}/versions   PromptVersionCreate -> PromptVersionResponse

    GET    /api/pipelines                -> PipelineListResponse
    GET    /api/pipelines/{pipeline_id}  -> PipelineDetailResponse

    POST   /api/runs                     RunCreate -> RunResponse
    GET    /api/runs/{run_id}            -> RunDetailResponse
    GET    /api/runs                     -> RunListResponse  (?target_id=&limit=)

    POST   /api/runs/{run_id}/feedback   FeedbackCreate -> FeedbackResponse
    POST   /api/runs/{run_id}/iterate    IterateRequest -> IterateResponse

    GET    /api/history/{prompt_id}      -> HistoryResponse
    POST   /api/history/{prompt_id}/rollback  RollbackRequest -> PromptVersionResponse

    GET    /api/settings                 -> SettingsResponse
    PUT    /api/settings/defaults        Defaults -> SettingsResponse
    GET    /api/settings/cost-estimate   ?prompt_id=&model=  -> CostEstimateResponse

    GET    /api/profiles                 -> list[Profile]
    POST   /api/profiles                 ProfileUpsertRequest -> Profile
    PUT    /api/profiles/{profile_id}    ProfileUpsertRequest -> Profile
    DELETE /api/profiles/{profile_id}    -> Profile
    POST   /api/profiles/{profile_id}/test -> ProfileTestResponse

    GET    /api/profile-presets          -> list[ProfilePreset]
    PUT    /api/profile-presets          ProfilePresetsUpdate -> list[ProfilePreset]
    DELETE /api/profile-presets          -> list[ProfilePreset]  (reset to seeded defaults)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aitap.scanner.models import (
    CallParameters,
    Confidence,
    Message,
    Pipeline,
    PromptSite,
    Provider,
    ProviderEvidence,
)


class _ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# ---------- Prompts ----------


class PromptSummary(_ApiModel):
    id: str
    name: str
    provider: Provider
    file: str
    line_start: int
    purpose: str | None
    confidence: Confidence
    latest_version: int


class PromptListResponse(_ApiModel):
    prompts: list[PromptSummary]


class PromptVersionInfo(_ApiModel):
    version: int
    note: str | None
    created_at: datetime
    created_by: Literal["human", "iteration"]
    parent_version: int | None


class PromptDetailResponse(_ApiModel):
    site: PromptSite
    versions: list[PromptVersionInfo]


class PromptVersionCreate(_ApiModel):
    messages: list[Message]
    parameters: CallParameters
    note: str | None = None
    parent_version: int | None = None


class PromptVersionResponse(_ApiModel):
    prompt_id: str
    version: int


# ---------- Pipelines ----------


class PipelineSummary(_ApiModel):
    id: str
    name: str
    node_count: int
    edge_count: int
    entry_count: int
    exit_count: int


class PipelineListResponse(_ApiModel):
    pipelines: list[PipelineSummary]


class PipelineDetailResponse(_ApiModel):
    pipeline: Pipeline
    site_index: dict[str, PromptSummary]  # prompt_id -> summary, for DAG node rendering


# ---------- Runs ----------


class DatasetCase(_ApiModel):
    """A single test case fed to a prompt or pipeline."""

    inputs: dict[str, object]
    expected_at: dict[str, object] | None = None  # for pipelines: expected output at named node


class RunCreate(_ApiModel):
    target_kind: Literal["prompt", "pipeline"]
    target_id: str
    target_version: int
    cases: list[DatasetCase] = Field(default_factory=list)
    dataset_id: str | None = None  # alternative to inline cases
    provider: Provider
    model: str
    parameters: CallParameters

    # ---- Pipeline run-mode selectors (ignored when target_kind == "prompt") ----
    #
    # ``pipeline_mode`` makes the run mode explicit on the wire instead of
    # inferring it from ``pipeline_segment`` (see wave-5-design.md A·D1).
    # The three modes map 1:1 to ``pipeline_runner.run_pipeline``'s modes:
    #
    #   - "node"        run a single node in isolation; needs pipeline_node_id.
    #   - "segment"     run a contiguous slice; needs a non-empty pipeline_segment.
    #   - "end_to_end"  walk the whole DAG (the historical default).
    #
    # ``None`` is the backward-compatible default: it behaves byte-for-byte
    # like "end_to_end" so existing clients that never send a mode keep their
    # current behaviour. The route layer (routes/runs.py) enforces the
    # field/mode consistency rules and 422s on violations; dispatch.py maps
    # these fields onto the runner.
    pipeline_mode: Literal["node", "segment", "end_to_end"] | None = None
    pipeline_node_id: str | None = None  # required when pipeline_mode == "node"
    pipeline_segment: list[str] | None = None  # required (non-empty) when mode == "segment"


class RunOutput(_ApiModel):
    case_index: int
    text: str | None = None
    image_path: str | None = None  # for image-generation prompts
    error: str | None = None
    intermediate: dict[str, str] | None = None  # node_id -> output, for pipelines


class RunResponse(_ApiModel):
    run_id: str
    status: Literal["running", "done", "failed"]


class RunDetailResponse(_ApiModel):
    run_id: str
    target_kind: Literal["prompt", "pipeline"]
    target_id: str
    target_version: int
    status: Literal["running", "done", "failed"]
    outputs: list[RunOutput]
    cost_usd: float
    started_at: datetime
    finished_at: datetime | None


class RunListResponse(_ApiModel):
    runs: list[RunResponse]


# ---------- Feedback / Iteration ----------


class FeedbackCreate(_ApiModel):
    case_index: int
    rating: Literal[-1, 0, 1] | None = None
    ideal_answer: str | None = None
    critique: str | None = None


class FeedbackResponse(_ApiModel):
    feedback_id: int


class IterateRequest(_ApiModel):
    """Trigger one round of self-iteration based on collected feedback for the run."""

    judge_model: str | None = None
    max_iterations: int = Field(default=3, ge=1, le=10)
    convergence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    include_downstream: bool = False  # impact-radius regression test for pipeline nodes


class IterateResponse(_ApiModel):
    new_version: int
    score_before: float | None
    score_after: float | None
    converged: bool
    downstream_impact: list[str] = Field(default_factory=list)  # affected prompt_ids


# ---------- History ----------


class HistoryEntry(_ApiModel):
    version: int
    note: str | None
    created_at: datetime
    created_by: Literal["human", "iteration"]
    parent_version: int | None
    avg_score: float | None


class HistoryResponse(_ApiModel):
    prompt_id: str
    entries: list[HistoryEntry]


class RollbackRequest(_ApiModel):
    target_version: int


# ---------- Settings ----------


class CostEstimateResponse(_ApiModel):
    estimated_tokens: int
    estimated_usd: float
    model: str


class Defaults(_ApiModel):
    """Per-process default profile selections for runs and the judge.

    ``None`` on either field is the documented "no default chosen yet"
    state. The Settings page surfaces it with a yellow Inventory banner
    (Decision 1 in ``docs/profiles-design.md``). The same shape is the
    request body for ``PUT /api/settings/defaults``.
    """

    model_profile_id: str | None = None
    judge_profile_id: str | None = None


class SettingsResponse(_ApiModel):
    """Snapshot of the current process's effective settings.

    The legacy provider-keyed ``keys`` array (``list[ProviderKeyStatus]``)
    is removed in contract v3. Per-profile key status now lives inline
    on each :class:`Profile` returned by ``GET /api/profiles``; clients
    that need to render key state read that endpoint instead. The
    legacy provider/model/judge_model fields are retained for
    backward-compat reading only — the UI no longer surfaces them
    after the multi-provider redesign.
    """

    provider: Provider
    model: str
    judge_model: str | None
    cost_per_run_usd: float
    cost_per_session_usd: float
    providers_available: list[ProviderEvidence]
    # Per-process default profile selections. Both fields are nullable;
    # ``None`` is the documented "no default chosen" sentinel, surfaced
    # as a yellow Inventory banner (Decision 1 in
    # ``docs/profiles-design.md``).
    defaults: Defaults = Field(default_factory=lambda: Defaults())


# ---------- Profiles (multi-provider redesign) ----------
#
# Contract v3: profile types are the only documented key-management
# surface. See ``docs/profiles-design.md`` for the design.


class Profile(_ApiModel):
    """One configured LLM endpoint, exactly as the Settings page renders it.

    The persistent fields (``id``, ``label``, ``base_url``, ``protocol``,
    ``model_id``, ``notes``) live in ``.aitap/config.yaml`` under
    ``profiles:``. The key-status triple (``key_configured``,
    ``key_source``, ``key_masked``) is *derived* per request from
    :mod:`aitap.secrets` — the raw key never appears on this model.
    """

    id: str
    label: str
    base_url: str
    protocol: Literal["openai-compat", "anthropic"]
    model_id: str
    notes: str = ""
    key_configured: bool
    # Profile-id keys never come from environment variables (env vars
    # like ANTHROPIC_API_KEY are tied to provider *names*, not profile
    # ids), so the Literal is narrower than the legacy key-source type.
    key_source: Literal["keyring", "fallback", "none"]
    key_masked: str | None = None


class ProfileUpsertRequest(_ApiModel):
    """Body for ``POST /api/profiles`` and ``PUT /api/profiles/{id}``.

    The ``label`` is free-text + user-editable; the route slugifies it
    into the ``id`` at creation time. On PUT, the ``id`` is fixed —
    relabelling a profile does NOT change its id (per the design doc),
    so the keyring entry and any cross-references stay stable.

    ``api_key`` is request-only and optional:

    - On POST: when present, the route immediately calls
      :func:`aitap.secrets.set_key_for_profile` with the new id; absent
      means "create the profile with no key yet, the user will add one
      later".
    - On PUT: when present, the route updates the keyring entry under
      the (unchanged) id; absent means "leave the existing key alone".

    ``use_fallback`` is the explicit opt-in to write the key into
    ``~/.aitap/secrets.yaml`` when the OS keyring is unusable. The route
    returns 409 + a plain-language detail when the keyring is down and
    this flag is false, so the UI can show a confirm dialog and re-POST
    with ``use_fallback=True``.
    """

    label: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    protocol: Literal["openai-compat", "anthropic"]
    model_id: str = Field(min_length=1)
    notes: str = ""
    api_key: str | None = None
    use_fallback: bool = False


class ProfileTestResponse(_ApiModel):
    """Result of ``POST /api/profiles/{profile_id}/test``.

    Connectivity probe outcome for one profile. ``ok=True`` means the
    minimal "ping" chat call returned a 2xx; ``ok=False`` reports a
    coarse reason so the UI can render the right plain-language
    remediation. ``detail`` is a human sentence (never a stack trace,
    never the key).
    """

    ok: bool
    reason: Literal["auth", "rate_limit", "network", "other"] | None = None
    detail: str | None = None


# ---------- Profile presets (chip templates on the Add Profile form) ----------
#
# Additive (CONTRACTS.md): a new ``ProfilePreset`` type + a tiny request
# wrapper used by the editor's "save the whole list" path. Decoupled
# from :class:`Profile` because presets are template *suggestions* (no
# key, no id of their own, no key-status triple) — they only carry the
# subset of fields the chip click pre-fills on the Add Profile form.
#
# Storage: ``.aitap/profile-presets.json`` (user-editable). See
# ``aitap.profile_presets`` for the seed-on-launch + load/save helpers.


class ProfilePreset(_ApiModel):
    """One template chip row on the Add Profile form.

    Clicking the chip pre-fills the form's ``base_url`` + ``protocol`` +
    ``model_id`` from this preset; the user still types a free-text
    ``label`` and pastes their key. ``name`` is the chip's display
    label (e.g. ``"DeepSeek"``); it is plain text, not a slug, because
    presets don't have stable ids — the user can rename or delete them
    freely via the Manage presets editor.
    """

    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    protocol: Literal["openai-compat", "anthropic"]
    model_id: str = Field(min_length=1)


class ProfilePresetsUpdate(_ApiModel):
    """Body for ``PUT /api/profile-presets``.

    Carries the whole new list — replace-in-full semantics. Per-row
    add/edit/delete operations happen client-side in the editor;
    persistence is a single round-trip on Save. Keeps the storage layer
    a flat JSON file the user can also edit by hand.
    """

    presets: list[ProfilePreset]


# ---------- Scan trigger (also used by audit) ----------


class ScanRequest(_ApiModel):
    path: str | None = None  # defaults to project_root
    deep: bool = False


class ScanResponse(_ApiModel):
    files_scanned: int
    prompt_count: int
    pipeline_count: int
    warnings: list[str]
