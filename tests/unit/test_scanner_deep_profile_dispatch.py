"""``aitap scan --deep`` profile-keyed dispatch.

PR #58 wired ``aitap scan --deep`` to honour the ``ANTHROPIC_BASE_URL``
env var so DeepSeek (Anthropic-protocol gateway) could ride the legacy
``register_provider("anthropic", ...)`` path. That was the smallest
unblocker; the cleaner answer — flagged as a follow-up since
``wt/profile-cleanup`` (PR #43) — is to migrate L2 onto the new
profile-keyed dispatch from PR #40, which gets DeepSeek / Moonshot /
Groq / Together / Qwen / SiliconFlow / Ollama / LM Studio for free
because they all flow through :class:`OpenAICompatClient` natively.

This file pins the behaviour of the new dispatch path inside
:func:`aitap.scanner._run_l2`:

Profile-selection order
-----------------------

1. Explicit ``--profile <id>`` flag wins.
2. ``settings.defaults.model_profile_id`` (the user-picked default).
3. The single configured profile (no ambiguity → no need to retype).
4. Legacy ``provider.name + provider.model`` fallback.

Error surface
-------------

- An explicit ``--profile`` that doesn't match any configured profile
  emits a plain-language warning naming the missing id and bails to
  L1 — NO silent fall-through to legacy.
- A profile whose API key isn't set in keyring / fallback file emits
  a plain-language warning naming the profile id and bails.
- An implicit (default / single-profile) miss also bails to L1 with
  a warning — same UX as the explicit case so a user with a stale
  default doesn't get a silent legacy dispatch surprise.

What we don't test here
-----------------------

The legacy path itself is covered by the existing
``test_scanner_init.py`` tests and the live cc-project eval.
:func:`aitap.deep.factory.get_client_for_profile_config` is covered
by ``tests/unit/test_deep_factory.py`` (the sibling that already
pinned ``get_client_for_profile``). This file only pins the *routing*
choice — which path L2 dispatches through, given the config + flag.
"""

from __future__ import annotations

import pytest

from aitap.config import DefaultsConfig, ProfileConfig, Settings
from aitap.scanner import _build_profile_client, _resolve_profile_id


def _profile(*, id: str = "deepseek-chat", protocol: str = "openai-compat") -> ProfileConfig:
    """Build a minimal :class:`ProfileConfig` for routing tests."""
    return ProfileConfig(
        id=id,
        label=f"Test {id}",
        base_url="https://api.example.com",
        protocol=protocol,  # type: ignore[arg-type]
        model_id="some-model-v1",
    )


# --------------------------------------------------------------------------- #
# _resolve_profile_id                                                         #
# --------------------------------------------------------------------------- #


def test_resolve_picks_explicit_flag_over_default() -> None:
    """The user-typed ``--profile X`` always wins, even when a default
    is configured. Pin so a refactor that swaps the order doesn't
    silently break the "pin a one-off run to a particular profile"
    use case.
    """
    settings = Settings(
        profiles=[_profile(id="explicit"), _profile(id="default")],
        defaults=DefaultsConfig(model_profile_id="default"),
    )
    assert _resolve_profile_id(settings, explicit="explicit") == "explicit"


def test_resolve_picks_default_when_no_flag() -> None:
    settings = Settings(
        profiles=[_profile(id="a"), _profile(id="default-pick")],
        defaults=DefaultsConfig(model_profile_id="default-pick"),
    )
    assert _resolve_profile_id(settings, explicit=None) == "default-pick"


def test_resolve_picks_single_profile_when_no_flag_no_default() -> None:
    """The "no need to retype" shortcut: one profile configured, no
    default set → use it. Saves the user a ``--profile`` flag in the
    common cc-project shape (one DeepSeek profile, nothing else).
    """
    settings = Settings(profiles=[_profile(id="solo")])
    assert _resolve_profile_id(settings, explicit=None) == "solo"


def test_resolve_returns_none_when_no_profiles_configured() -> None:
    """Zero profiles configured + no flag → ``None``. The caller treats
    this as the legacy signal: there's no profile to dispatch through,
    fall through to the legacy ``provider.name + provider.model`` path.
    """
    empty = Settings()
    assert _resolve_profile_id(empty, explicit=None) is None


def test_resolve_returns_none_when_multiple_profiles_no_default() -> None:
    """Two-or-more profiles configured + no flag + no default → ``None``.

    The intent here is ambiguity, NOT "use the first one" or "fall
    through to legacy silently". ``_run_l2`` handles the ``None`` →
    ambiguity case with a plain-language warning that tells the user to
    set a default or pass ``--profile``; the legacy path still runs as
    a last resort but only after the user sees the diagnostic.

    Pin the resolver's behaviour separately from the routing decision
    so a refactor that conflates "ambiguous" and "no profiles" trips
    a test here.
    """
    settings = Settings(profiles=[_profile(id="a"), _profile(id="b")])
    assert _resolve_profile_id(settings, explicit=None) is None


# --------------------------------------------------------------------------- #
# _build_profile_client                                                       #
# --------------------------------------------------------------------------- #


def test_build_profile_client_warns_when_profile_id_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit --profile pointing at a missing id warns with the
    missing id named, no stack trace, no silent fallback.
    """
    settings = Settings(profiles=[_profile(id="real")])
    client = _build_profile_client(settings, "ghost", explicit=True, json_mode=False)
    assert client is None
    err = capsys.readouterr().err
    assert "'ghost'" in err
    assert "--profile flag" in err
    assert "Open Settings" in err


def test_build_profile_client_warns_when_default_points_at_missing_profile(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An implicit default pointing at a missing id uses a sentence
    that names the default (not "your --profile flag") so the user
    knows which knob to fix.
    """
    settings = Settings(profiles=[_profile(id="real")])
    client = _build_profile_client(settings, "stale-default", explicit=False, json_mode=False)
    assert client is None
    err = capsys.readouterr().err
    assert "'stale-default'" in err
    assert "default profile" in err


def test_build_profile_client_warns_when_key_not_set(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real configured profile whose API key isn't in keyring /
    fallback file → warn naming the profile id + name the fix
    locations (Settings → Test, or ``~/.aitap/secrets.yaml``).
    """
    settings = Settings(profiles=[_profile(id="keyless")])

    # Stub out get_key_for_profile to simulate "no key set".
    from aitap import secrets as secrets_module

    monkeypatch.setattr(secrets_module, "get_key_for_profile", lambda _: None)

    client = _build_profile_client(settings, "keyless", explicit=True, json_mode=False)
    assert client is None
    err = capsys.readouterr().err
    assert "'keyless'" in err
    assert "API key" in err
    assert "~/.aitap/secrets.yaml" in err


def test_build_profile_client_returns_client_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the profile resolves and the key is set, the helper
    returns a real :class:`LLMClient` instance built via
    ``get_client_for_profile_config``.
    """
    settings = Settings(profiles=[_profile(id="deepseek-chat")])

    from aitap import secrets as secrets_module
    from aitap.deep.client import LLMClient

    monkeypatch.setattr(secrets_module, "get_key_for_profile", lambda _: "sk-FAKE-deepseek")

    client = _build_profile_client(settings, "deepseek-chat", explicit=True, json_mode=False)
    assert isinstance(client, LLMClient)


def test_build_profile_client_still_warns_on_stderr_in_json_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON output mode keeps the diagnostic on stderr. The JSON
    consumer reads stdout (where ``ScanResult.model_dump_json`` lands),
    so the warning doesn't pollute the JSON payload — but the human
    user still learns that ``--deep --profile X`` got downgraded to
    L1 because X isn't configured. Silent JSON-mode degradation was
    the M1 reviewer flag on PR #61.
    """
    settings = Settings(profiles=[_profile(id="real")])
    client = _build_profile_client(settings, "ghost", explicit=True, json_mode=True)
    assert client is None
    err = capsys.readouterr().err
    # Same diagnostic as the non-JSON path — stderr stays informative.
    assert "'ghost'" in err
    assert "Open Settings" in err


# --------------------------------------------------------------------------- #
# CLI guards (typer parameter validation, not helper-level)                   #
# --------------------------------------------------------------------------- #


def test_scan_rejects_profile_without_deep() -> None:
    """``--profile`` only makes sense alongside ``--deep``. Passing it
    bare should fail loudly so the user doesn't think the flag took
    effect. Mirrors the existing ``--rules-only + --deep`` mutual-
    exclusion test in ``test_scanner_init.py``.

    We assert on the substantive bits (both flag names appear in the
    error panel) rather than the exact docstring sentence, because
    Click's BadParameter renders inside a fixed-width ``╭─Error─╮``
    panel that line-wraps on the CI runner's default 80-column terminal
    — a literal-substring match would break each time typer's wrapping
    heuristic changes.
    """
    import re

    import typer
    from typer.testing import CliRunner

    from aitap.scanner import scan_command

    app = typer.Typer()
    app.command("scan")(scan_command)
    # A second command is required by typer when there's only one
    # subcommand and we want runner.invoke to dispatch by name.
    app.command("noop")(lambda: None)

    runner = CliRunner()
    fixture = (
        pytest.importorskip("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures"
        / "openai_basic"
    )
    result = runner.invoke(app, ["scan", str(fixture), "--profile", "deepseek-chat"])
    assert result.exit_code != 0
    # Combine stdout + stderr, strip ANSI codes, collapse whitespace
    # (panel wrapping inserts newlines + box-drawing characters).
    raw = (result.stdout or "") + (result.stderr or "")
    no_ansi = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    flat = re.sub(r"\s+", " ", no_ansi)
    assert "--profile" in flat
    assert "--deep" in flat


# --------------------------------------------------------------------------- #
# End-to-end ambiguity guard (M3 from PR #61 review)                          #
# --------------------------------------------------------------------------- #


def test_run_l2_warns_on_multiple_profiles_without_default(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two configured profiles + no flag + no default → ``_run_l2``
    must emit an ambiguity warning that names the configured profile
    ids and tells the user which knob to turn (set a default or pass
    ``--profile``). Silently falling through to legacy here would
    surprise a user who configured profiles thinking they were *the*
    L2 path.
    """
    from aitap.scanner import _run_l2
    from aitap.scanner.models import ScanResult

    # Stub Settings so the resolver sees a multi-profile + no-default
    # state. We monkeypatch the module-level Settings constructor — the
    # function does ``from aitap.config import Settings`` at runtime,
    # so patching `aitap.config.Settings` reaches it.
    def fake_settings() -> Settings:
        return Settings(
            profiles=[_profile(id="alpha"), _profile(id="beta")],
        )

    import aitap.config

    monkeypatch.setattr(aitap.config, "Settings", fake_settings)

    empty_result = ScanResult(
        project_root=".",
        files_scanned=0,
        prompts=[],
        pipelines=[],
        providers_detected=[],
    )

    returned = _run_l2(
        empty_result,
        auto_approve=False,
        json_mode=False,
        profile_id=None,
    )

    # L1 result is returned unchanged.
    assert returned is empty_result
    err = capsys.readouterr().err
    assert "found 2 configured profiles" in err
    assert "'alpha'" in err and "'beta'" in err
    assert "--profile" in err and "default" in err
