"""Unit tests for ``aitap.profile_presets``.

The module owns the seed-on-launch + load/save/reset behaviour for the
chip-row templates on the Add Profile form (see
``docs/profiles-design.md`` Decision 4). These tests pin:

- The 11 documented presets exist + carry the right shape.
- ``load_presets`` seeds on first launch when ``.aitap/`` exists.
- ``load_presets`` returns the seed in-memory only when ``.aitap/`` is
  absent (the project wasn't initialised yet).
- ``save_presets`` round-trips arbitrary user-edited lists.
- ``load_presets`` survives a corrupted JSON file (logs + fallback).
- ``reset_presets`` overwrites whatever the user had and returns the
  fresh seeded list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitap import profile_presets
from aitap.config import Settings
from aitap.server.routes import ProfilePreset


@pytest.fixture()
def settings_in_tmp(tmp_path: Path) -> Settings:
    """A throwaway Settings whose project_root is the tmp dir.

    The ``.aitap/`` directory is created so the seed-on-launch path is
    exercised by default; tests that need the "no .aitap/" path nuke it
    explicitly.
    """
    aitap_dir = tmp_path / ".aitap"
    aitap_dir.mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


# ---------------------------------------------------------------------------
# Seeded set shape
# ---------------------------------------------------------------------------


def test_seeded_set_has_exactly_eleven_documented_presets(
    settings_in_tmp: Settings,
) -> None:
    """The design doc nails down 11 starter rows — drift here means the
    doc and the code disagree on what a "first launch" gives you."""
    seeded = profile_presets.load_presets(settings_in_tmp)
    assert len(seeded) == 11


def test_seeded_set_contains_each_documented_vendor(
    settings_in_tmp: Settings,
) -> None:
    """Spot-check each row by name + protocol. Catches a swap
    (Anthropic accidentally tagged as ``openai-compat``) or an
    omission (someone deleted Together in a refactor)."""
    seeded = profile_presets.load_presets(settings_in_tmp)
    by_name = {p.name: p for p in seeded}

    # Anthropic is the only ``anthropic``-protocol row in the seed.
    assert by_name["Anthropic"].protocol == "anthropic"
    assert by_name["Anthropic"].base_url == "https://api.anthropic.com"

    # Every other vendor speaks the OpenAI chat-completions wire.
    openai_compat_names = {
        "OpenAI",
        "DeepSeek",
        "Moonshot (Kimi)",
        "MiMo (Xiaomi)",
        "Groq",
        "Together",
        "Qwen / DashScope",
        "SiliconFlow",
        "Ollama (local)",
        "LM Studio (local)",
    }
    for name in openai_compat_names:
        assert name in by_name, f"missing seeded preset: {name!r}"
        assert by_name[name].protocol == "openai-compat", name


def test_seeded_set_returns_fresh_instances_each_call(
    settings_in_tmp: Settings,
) -> None:
    """Mutating one caller's list must not corrupt the module-level seed.

    Defensive: we hand back ``ProfilePreset`` instances which are
    pydantic models (immutable-ish), but the *list* itself is a fresh
    container each call so a caller popping or extending it can't drift
    the source-of-truth for the next caller.
    """
    first = profile_presets.load_presets(settings_in_tmp)
    first.pop()
    second = profile_presets.load_presets(settings_in_tmp)
    # The on-disk file was seeded on the first call, so the second load
    # reads it back rather than re-seeding. The point of the assertion
    # is that ``first.pop()`` did NOT bleed into the second call's
    # result via a shared list.
    assert len(second) == 11


# ---------------------------------------------------------------------------
# load_presets behaviour by state
# ---------------------------------------------------------------------------


def test_load_seeds_on_first_launch_when_aitap_dir_exists(
    settings_in_tmp: Settings,
) -> None:
    """First load with no presets file → seed + persist."""
    path = settings_in_tmp.project_root / ".aitap" / "profile-presets.json"
    assert not path.exists()

    seeded = profile_presets.load_presets(settings_in_tmp)
    assert len(seeded) == 11
    # The seed was persisted so subsequent loads (or a hand-editor)
    # find a real file rather than re-seeding.
    assert path.is_file()


def test_load_returns_seeded_list_in_memory_when_no_aitap_dir(
    tmp_path: Path,
) -> None:
    """Without ``.aitap/`` we must NOT create files outside the user's
    intent — just hand back the seeded list so the chip row works."""
    settings = Settings(project_root=tmp_path)
    # Explicitly do not create ``.aitap/``.
    assert not (tmp_path / ".aitap").exists()

    seeded = profile_presets.load_presets(settings)
    assert len(seeded) == 11
    # No file was created.
    assert not (tmp_path / ".aitap").exists()


def test_load_round_trips_user_edits(settings_in_tmp: Settings) -> None:
    """Saving a custom list → loading sees exactly that list (no re-seed)."""
    custom = [
        ProfilePreset(
            name="Internal gateway",
            base_url="https://gateway.corp/v1",
            protocol="openai-compat",
            model_id="internal-llama",
        ),
        ProfilePreset(
            name="Anthropic via proxy",
            base_url="https://gateway.corp/anthropic",
            protocol="anthropic",
            model_id="claude-haiku-4-5",
        ),
    ]
    assert profile_presets.save_presets(settings_in_tmp, custom)

    loaded = profile_presets.load_presets(settings_in_tmp)
    assert [p.model_dump() for p in loaded] == [p.model_dump() for p in custom]


def test_load_falls_back_to_seed_on_corrupted_json(
    settings_in_tmp: Settings,
) -> None:
    """A busted file must NOT take down the Settings page — we log and
    return the seed instead, leaving the file untouched so a hand-edit
    isn't clobbered.
    """
    path = settings_in_tmp.project_root / ".aitap" / "profile-presets.json"
    path.write_text("{not valid json", encoding="utf-8")

    seeded = profile_presets.load_presets(settings_in_tmp)
    assert len(seeded) == 11
    # File still on disk in its broken state — we don't auto-overwrite.
    assert path.read_text(encoding="utf-8") == "{not valid json"


def test_load_falls_back_to_seed_on_wrong_top_level_shape(
    settings_in_tmp: Settings,
) -> None:
    """A non-list top level (someone wrote ``{}`` by hand) gets the
    same defence-in-depth fallback as a parse error."""
    path = settings_in_tmp.project_root / ".aitap" / "profile-presets.json"
    path.write_text("{}", encoding="utf-8")

    seeded = profile_presets.load_presets(settings_in_tmp)
    assert len(seeded) == 11


def test_load_skips_malformed_rows_but_keeps_good_ones(
    settings_in_tmp: Settings,
) -> None:
    """One bad row shouldn't sink the rest of the list."""
    path = settings_in_tmp.project_root / ".aitap" / "profile-presets.json"
    path.write_text(
        # Good row, then a row missing ``model_id``, then another good row.
        '[{"name":"Good","base_url":"https://a/v1","protocol":"openai-compat","model_id":"m"},'
        '{"name":"Bad","base_url":"https://b/v1","protocol":"openai-compat"},'
        '{"name":"Good2","base_url":"https://c/v1","protocol":"anthropic","model_id":"x"}]',
        encoding="utf-8",
    )

    loaded = profile_presets.load_presets(settings_in_tmp)
    names = [p.name for p in loaded]
    assert names == ["Good", "Good2"]


# ---------------------------------------------------------------------------
# save_presets behaviour
# ---------------------------------------------------------------------------


def test_save_returns_false_when_no_aitap_dir(tmp_path: Path) -> None:
    """No ``.aitap/`` → save is a no-op + returns False (caller logs
    and keeps the in-memory list)."""
    settings = Settings(project_root=tmp_path)
    presets = [
        ProfilePreset(name="x", base_url="https://x/v1", protocol="openai-compat", model_id="m")
    ]
    assert profile_presets.save_presets(settings, presets) is False


def test_save_writes_empty_list_when_user_clears_everything(
    settings_in_tmp: Settings,
) -> None:
    """Explicit empty list is a valid persisted state — distinct from
    "fall back to seed". Reset is a separate operation."""
    assert profile_presets.save_presets(settings_in_tmp, [])
    loaded = profile_presets.load_presets(settings_in_tmp)
    assert loaded == []


def test_save_preserves_non_ascii_names_via_ensure_ascii_false(
    settings_in_tmp: Settings,
) -> None:
    """A Chinese chip name for a private gateway must round-trip
    without ``\\uXXXX`` escapes — the file is human-edited."""
    presets = [
        ProfilePreset(
            name="内部网关",
            base_url="https://gateway.corp/v1",
            protocol="openai-compat",
            model_id="m",
        ),
    ]
    profile_presets.save_presets(settings_in_tmp, presets)
    raw = (settings_in_tmp.project_root / ".aitap" / "profile-presets.json").read_text(
        encoding="utf-8"
    )
    assert "内部网关" in raw


# ---------------------------------------------------------------------------
# reset_presets behaviour
# ---------------------------------------------------------------------------


def test_reset_replaces_user_edits_with_seeded_list(
    settings_in_tmp: Settings,
) -> None:
    """User saved a custom list → Reset clears it back to the seed."""
    profile_presets.save_presets(
        settings_in_tmp,
        [
            ProfilePreset(
                name="only one",
                base_url="https://x/v1",
                protocol="openai-compat",
                model_id="m",
            )
        ],
    )

    seeded = profile_presets.reset_presets(settings_in_tmp)
    assert len(seeded) == 11

    # Persisted shape matches the in-memory return.
    loaded = profile_presets.load_presets(settings_in_tmp)
    assert len(loaded) == 11
    assert [p.name for p in loaded] == [p.name for p in seeded]


def test_reset_is_idempotent(settings_in_tmp: Settings) -> None:
    """Calling reset twice in a row produces the same on-disk content."""
    first = profile_presets.reset_presets(settings_in_tmp)
    second = profile_presets.reset_presets(settings_in_tmp)
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]
