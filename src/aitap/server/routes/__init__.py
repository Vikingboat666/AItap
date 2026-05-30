"""HTTP API contract.

Contract version: 1 (2026-05-09)

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
    PUT    /api/settings                 SettingsUpdate -> SettingsResponse
    GET    /api/settings/cost-estimate   ?prompt_id=&model=  -> CostEstimateResponse

    POST   /api/settings/key             SetKeyRequest -> ProviderKeyStatus
    DELETE /api/settings/key/{provider}  -> ProviderKeyStatus
    POST   /api/settings/test/{provider} -> TestKeyResponse
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


class ProviderKeyStatus(_ApiModel):
    """Per-provider API-key state for the Settings page.

    Additive (CONTRACTS.md): new type, not a rename. The frontend reads
    this off ``SettingsResponse.keys``; the raw key value never appears
    on any response, only the masked preview.
    """

    provider: Literal["anthropic", "openai"]
    configured: bool
    source: Literal["keyring", "fallback", "env", "none"]
    masked: str | None = None  # e.g. "sk-ant-...XXXX"; null when unconfigured


class SettingsResponse(_ApiModel):
    provider: Provider
    model: str
    judge_model: str | None
    cost_per_run_usd: float
    cost_per_session_usd: float
    providers_available: list[ProviderEvidence]
    # Additive: per-provider key status. Default empty so old clients
    # that don't ask about it still get a well-shaped response.
    keys: list[ProviderKeyStatus] = Field(default_factory=list)


class SettingsUpdate(_ApiModel):
    provider: Provider | None = None
    model: str | None = None
    judge_model: str | None = None
    cost_per_run_usd: float | None = None
    cost_per_session_usd: float | None = None


class SetKeyRequest(_ApiModel):
    """Body for ``POST /api/settings/key``.

    The raw ``key`` is request-only — it is **never** echoed on the
    response, never logged, and never persisted into the SQLite store.
    The response is a :class:`ProviderKeyStatus` containing only
    metadata.
    """

    provider: Literal["anthropic", "openai"]
    key: str = Field(min_length=1)
    # When False (the default), the route persists via the OS keyring and
    # 409s the request if the keyring is unusable. The UI then shows a
    # "your keychain isn't available — save to a file instead?" confirm
    # dialog and re-POSTs with use_fallback=True. This keeps the silent
    # fallback path the security model forbids out of the contract.
    use_fallback: bool = False


class TestKeyResponse(_ApiModel):
    """Result of ``POST /api/settings/test/{provider}``.

    ``ok=True`` means the minimal probe call (Anthropic ``/v1/messages``
    or OpenAI ``chat.completions`` with a single ``"ping"`` and
    ``max_tokens=4``) returned a 2xx. ``ok=False`` reports a coarse
    reason so the UI can render the right plain-language remediation;
    ``detail`` is a human sentence (never a stack trace, never the key).
    """

    ok: bool
    reason: Literal["auth", "rate_limit", "network", "other"] | None = None
    detail: str | None = None


# ---------- Scan trigger (also used by audit) ----------


class ScanRequest(_ApiModel):
    path: str | None = None  # defaults to project_root
    deep: bool = False


class ScanResponse(_ApiModel):
    files_scanned: int
    prompt_count: int
    pipeline_count: int
    warnings: list[str]
