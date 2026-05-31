"""Tests for the ``.aitap/config.yaml`` profile-block round-trip.

Coverage:

- Empty / missing config → loader returns empty lists/defaults.
- Round-trip preserves the documented profile shape.
- Defaults block round-trips independently of profiles.
- Corrupted YAML degrades to empty + warning, never raises.
- The legacy ``provider:`` block is preserved across writes (key
  regression — the multi-provider redesign must not break existing
  installs while wt/profile-cleanup is still pending).
- Saving when the file doesn't exist skips silently (memory-only
  fallback per design doc).
- A single malformed profile entry is dropped, the rest survive.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from aitap.config import DefaultsConfig, ProfileConfig, Settings
from aitap.config_io import (
    load_profiles_from_yaml,
    save_defaults_to_yaml,
    save_profiles_to_yaml,
)


def _settings(tmp_path: Path) -> Settings:
    """Build a :class:`Settings` rooted at *tmp_path*."""
    return Settings(project_root=tmp_path, aitap_dir=Path(".aitap"))


def _config_path(settings: Settings) -> Path:
    return settings.project_root / settings.aitap_dir / "config.yaml"


def _seed_config(settings: Settings, body: dict[str, object]) -> Path:
    """Write a YAML body into the test project's ``.aitap/config.yaml``."""
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    profiles, defaults = load_profiles_from_yaml(settings)
    assert profiles == []
    assert defaults == DefaultsConfig()


def test_load_existing_file_returns_parsed_profiles(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_config(
        settings,
        {
            "profiles": [
                {
                    "id": "openai-main",
                    "label": "OpenAI main",
                    "base_url": "https://api.openai.com/v1",
                    "protocol": "openai-compat",
                    "model_id": "gpt-4o-mini",
                    "notes": "",
                },
                {
                    "id": "anthropic-personal",
                    "label": "Anthropic personal",
                    "base_url": "https://api.anthropic.com",
                    "protocol": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "notes": "billing capped at $5/mo",
                },
            ],
            "defaults": {
                "model_profile_id": "openai-main",
                "judge_profile_id": None,
            },
        },
    )

    profiles, defaults = load_profiles_from_yaml(settings)
    assert [p.id for p in profiles] == ["openai-main", "anthropic-personal"]
    assert profiles[1].notes == "billing capped at $5/mo"
    assert profiles[1].protocol == "anthropic"
    assert defaults.model_profile_id == "openai-main"
    assert defaults.judge_profile_id is None


def test_load_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unterminated mapping: YAML chokes on it.
    path.write_text("profiles: [", encoding="utf-8")

    profiles, defaults = load_profiles_from_yaml(settings)
    assert profiles == []
    assert defaults == DefaultsConfig()


def test_load_skips_malformed_profile_entries(tmp_path: Path) -> None:
    """A single bad row shouldn't poison the whole list — the rest of
    the rows still parse, and the Inventory banner can flag the loss
    in a follow-up worktree."""
    settings = _settings(tmp_path)
    _seed_config(
        settings,
        {
            "profiles": [
                {
                    "id": "good-one",
                    "label": "Good",
                    "base_url": "https://api.example.com/v1",
                    "protocol": "openai-compat",
                    "model_id": "good-model",
                },
                {
                    # Missing required fields — invalid.
                    "id": "bad-one",
                },
                "not-even-a-dict",
            ],
        },
    )
    profiles, _ = load_profiles_from_yaml(settings)
    assert [p.id for p in profiles] == ["good-one"]


def test_load_malformed_defaults_block_falls_back_to_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_config(
        settings,
        {
            "defaults": {
                "model_profile_id": ["not", "a", "string"],
            },
        },
    )
    _, defaults = load_profiles_from_yaml(settings)
    assert defaults == DefaultsConfig()


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def test_save_writes_profiles_and_defaults(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_config(settings, {"provider": {"name": "anthropic", "model": "x"}})

    profiles = [
        ProfileConfig(
            id="kimi-1",
            label="Kimi K2",
            base_url="https://api.moonshot.cn/v1",
            protocol="openai-compat",
            model_id="moonshot-v1-32k",
        )
    ]
    defaults = DefaultsConfig(model_profile_id="kimi-1", judge_profile_id="kimi-1")

    assert save_profiles_to_yaml(settings, profiles, defaults) is True

    again, defaults_again = load_profiles_from_yaml(settings)
    assert again == profiles
    assert defaults_again == defaults


def test_save_preserves_legacy_provider_block(tmp_path: Path) -> None:
    """Regression guard for the staged migration: writing the new
    ``profiles`` / ``defaults`` blocks must not nuke the legacy
    ``provider`` block that the settings-routes layer still
    persists."""
    settings = _settings(tmp_path)
    _seed_config(
        settings,
        {
            "provider": {
                "name": "anthropic",
                "model": "claude-sonnet-4-6",
                "judge_model": None,
            },
            "cost": {"per_run_usd": 0.5, "per_session_usd": 5.0},
        },
    )

    profiles = [
        ProfileConfig(
            id="openai-main",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            protocol="openai-compat",
            model_id="gpt-4o-mini",
        )
    ]
    save_profiles_to_yaml(settings, profiles, DefaultsConfig())

    raw = yaml.safe_load(_config_path(settings).read_text(encoding="utf-8"))
    assert raw["provider"] == {
        "name": "anthropic",
        "model": "claude-sonnet-4-6",
        "judge_model": None,
    }
    assert raw["cost"] == {"per_run_usd": 0.5, "per_session_usd": 5.0}
    # And the new blocks are present too.
    assert len(raw["profiles"]) == 1
    assert raw["defaults"] == {
        "model_profile_id": None,
        "judge_profile_id": None,
    }


def test_save_skips_silently_when_no_aitap_dir(tmp_path: Path) -> None:
    """No ``.aitap/`` at all means the user never ran ``aitap init``
    here — we refuse to materialise files outside their explicit intent.
    The runtime override still applies for the rest of the process."""
    settings = _settings(tmp_path)
    assert not (tmp_path / ".aitap").exists()  # sanity
    assert save_profiles_to_yaml(settings, [], DefaultsConfig()) is False
    assert not _config_path(settings).exists()


def test_save_creates_config_yaml_on_first_write_when_aitap_dir_exists(
    tmp_path: Path,
) -> None:
    """``aitap init`` created ``.aitap/`` but no ``config.yaml`` yet
    (e.g. user deleted the default file, or upgraded from an older
    layout). The first profile save must materialise the file so the
    UI's "Saved." message isn't a lie. Without this fix, the user would
    add a profile in the UI, see success, restart, and find nothing
    persisted."""
    settings = _settings(tmp_path)
    (tmp_path / ".aitap").mkdir(parents=True)
    assert not _config_path(settings).exists()  # sanity

    profile = ProfileConfig(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        protocol="openai-compat",
        model_id="deepseek-chat",
    )
    saved = save_profiles_to_yaml(
        settings,
        [profile],
        DefaultsConfig(model_profile_id="deepseek"),
    )
    assert saved is True
    assert _config_path(settings).exists()

    # The fresh file only carries the two sections this module owns —
    # no legacy ``provider:`` block manufactured from nothing.
    data = yaml.safe_load(_config_path(settings).read_text(encoding="utf-8"))
    assert set(data.keys()) == {"profiles", "defaults"}
    assert data["profiles"][0]["id"] == "deepseek"
    assert data["defaults"]["model_profile_id"] == "deepseek"


def test_save_skips_on_corrupted_existing_file(tmp_path: Path) -> None:
    """If the on-disk file is unreadable, refuse to clobber it from
    scratch — that would nuke the user's other config keys."""
    settings = _settings(tmp_path)
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("profiles: [", encoding="utf-8")  # malformed

    assert save_profiles_to_yaml(settings, [], DefaultsConfig()) is False
    # Original (corrupt) bytes are still on disk.
    assert path.read_text(encoding="utf-8") == "profiles: ["


def test_save_defaults_only_round_trip(tmp_path: Path) -> None:
    """``save_defaults_to_yaml`` writes just the defaults block and
    leaves the profiles list alone."""
    settings = _settings(tmp_path)
    # Seed an existing config.yaml so the persistence helpers don't
    # skip with "no config.yaml here" — that path is exercised in
    # test_save_skips_silently_when_no_config_file separately.
    _seed_config(settings, {"provider": {"name": "anthropic", "model": "x"}})

    profiles = [
        ProfileConfig(
            id="openai-main",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            protocol="openai-compat",
            model_id="gpt-4o-mini",
        )
    ]
    save_profiles_to_yaml(settings, profiles, DefaultsConfig())

    # Now tweak only defaults.
    new_defaults = DefaultsConfig(
        model_profile_id="openai-main",
        judge_profile_id=None,
    )
    assert save_defaults_to_yaml(settings, new_defaults) is True

    profiles_again, defaults_again = load_profiles_from_yaml(settings)
    assert profiles_again == profiles  # profile list untouched
    assert defaults_again == new_defaults


def test_save_defaults_skips_silently_when_no_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert save_defaults_to_yaml(settings, DefaultsConfig()) is False


# ---------------------------------------------------------------------------
# Settings schema sanity
# ---------------------------------------------------------------------------


def test_settings_defaults_when_no_overrides(tmp_path: Path) -> None:
    """The new fields on :class:`Settings` carry sensible defaults so
    ``Settings()`` still works without a YAML present — important for
    every other test fixture that does ``Settings(project_root=...)``."""
    settings = _settings(tmp_path)
    assert settings.profiles == []
    assert settings.defaults == DefaultsConfig()
    # Legacy fields still present (regression guard).
    assert settings.provider.name == "anthropic"
    assert settings.cost.per_run_usd > 0
