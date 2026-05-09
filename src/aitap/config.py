"""Global runtime config loaded from .aitap/config.yaml + env."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CostLimits(BaseModel):
    """Hard caps to prevent runaway LLM spend."""

    per_run_usd: float = Field(default=1.00, ge=0.0)
    per_session_usd: float = Field(default=10.00, ge=0.0)


class ProviderConfig(BaseModel):
    """Default provider for L2 / iteration / judge calls."""

    name: str = Field(default="anthropic")  # "anthropic" | "openai"
    model: str = Field(default="claude-sonnet-4-6")
    judge_model: str | None = None  # falls back to `model` if None


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
