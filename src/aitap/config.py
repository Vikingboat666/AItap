"""Global runtime config loaded from .aitap/config.yaml + env."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CostLimits(BaseModel):
    """Hard caps to prevent runaway LLM spend."""

    per_run_usd: float = Field(default=1.00, ge=0.0)
    per_session_usd: float = Field(default=10.00, ge=0.0)


class ProviderConfig(BaseModel):
    """Default provider for L2 / iteration / judge calls.

    Legacy two-provider shape — preserved during the multi-provider
    redesign rollout. New code goes through :class:`ProfileConfig` +
    :class:`DefaultsConfig` instead; the two coexist until
    wt/profile-cleanup retires this block.
    """

    name: str = Field(default="anthropic")  # "anthropic" | "openai"
    model: str = Field(default="claude-sonnet-4-6")
    judge_model: str | None = None  # falls back to `model` if None


class ProfileConfig(BaseModel):
    """One user-defined LLM endpoint profile.

    Mirrors the API-facing ``Profile`` model in ``server/routes/__init__.py``
    but without the key-status fields — those are derived at request
    time from :mod:`aitap.secrets`. The persistent part lives here
    (``id``, ``label``, ``base_url``, ``protocol``, ``model_id``,
    ``notes``); the secret lives in the OS keyring under
    ``profile:<id>``.

    See ``docs/profiles-design.md`` §"Data model" for the full shape.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    protocol: Literal["openai-compat", "anthropic"]
    model_id: str = Field(min_length=1)
    notes: str = ""


class DefaultsConfig(BaseModel):
    """Per-process default profile selections for runs and the judge.

    Both fields are optional — ``None`` is the documented "no default
    chosen yet" state, surfaced as a yellow Inventory banner in the UI
    (see Decision 1 in ``docs/profiles-design.md``).
    """

    model_profile_id: str | None = None
    judge_profile_id: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AITAP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    project_root: Path = Field(default_factory=Path.cwd)
    aitap_dir: Path = Field(default=Path(".aitap"))
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    cost: CostLimits = Field(default_factory=CostLimits)
    # New profile-list pool + per-process defaults. Additive: the legacy
    # ``provider`` block above stays put until wt/profile-cleanup.
    profiles: list[ProfileConfig] = Field(default_factory=list)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)

    @property
    def db_path(self) -> Path:
        return self.project_root / self.aitap_dir / "db.sqlite"

    @property
    def prompts_dir(self) -> Path:
        return self.project_root / self.aitap_dir / "prompts"

    @property
    def pipelines_dir(self) -> Path:
        return self.project_root / self.aitap_dir / "pipelines"

    @property
    def datasets_dir(self) -> Path:
        return self.project_root / self.aitap_dir / "datasets"

    @property
    def runs_dir(self) -> Path:
        return self.project_root / self.aitap_dir / "runs"
