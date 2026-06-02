"""User-editable profile-preset templates for the Add Profile form.

The Add Profile form shows a row of "Start from a template" chips.
Clicking a chip pre-fills ``base_url`` + ``protocol`` + a suggested
``model_id`` — the user still types a free-text label and pastes their
key. This module owns the 11-row starter set and the JSON storage that
makes the list user-editable.

Design contract (``docs/profiles-design.md`` Decision 4):

- **Seed on first launch.** When ``.aitap/profile-presets.json`` does
  not exist, we materialise it with the 11 starter rows so a brand-new
  install gets the same default experience the design doc promises.
- **User-editable thereafter.** Subsequent loads read whatever the file
  contains — including hand-edits made via ``$EDITOR`` outside the UI.
  The Manage presets editor on the Settings page is a convenience
  wrapper around the same file.
- **Reset-to-defaults.** Deleting the file (via the editor or by hand)
  triggers a re-seed on the next load. The HTTP layer exposes this as
  ``DELETE /api/profile-presets``.

Storage shape — flat JSON, one preset per row, sorted by ``name`` on
disk so version-control diffs stay readable:

.. code-block:: json

    [
      {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "protocol": "anthropic",
        "model_id": "claude-sonnet-4-6"
      },
      ...
    ]

Safe-fail: a corrupted or unparseable file falls back to the seeded
list rather than raising — the Settings page must keep working even
when the user accidentally breaks the file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from aitap.config import Settings
from aitap.server.routes import ProfilePreset

# Mirror of the ``protocol`` Literal on the routes/__init__.py contract.
# Pinning the type here means the seed rows below type-check without a
# ``# type: ignore`` and pyright catches a typo (e.g. ``"openai-Compat"``)
# at edit time rather than at first launch. A future protocol-enum rename
# will surface as a static error on this alias too.
ProfileProtocol = Literal["openai-compat", "anthropic"]

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Seeded starter set (Decision 4 in ``docs/profiles-design.md``)
# ---------------------------------------------------------------------------
#
# Frozen tuple so callers can't mutate the source-of-truth by accident.
# Materialised into ProfilePreset instances on demand via
# :func:`_seeded_presets`. Adding a new vendor: append a row here,
# bump the seed-version comment if you're shipping a release that
# expects the list to grow on upgrade (currently we only seed on first
# launch — existing installs keep their hand-edited list).
_SEEDED_PRESETS: tuple[tuple[str, str, ProfileProtocol, str], ...] = (
    # (name, base_url, protocol, suggested model_id)
    ("Anthropic", "https://api.anthropic.com", "anthropic", "claude-sonnet-4-6"),
    ("OpenAI", "https://api.openai.com/v1", "openai-compat", "gpt-4o-mini"),
    ("DeepSeek", "https://api.deepseek.com/v1", "openai-compat", "deepseek-chat"),
    (
        "Moonshot (Kimi)",
        "https://api.moonshot.cn/v1",
        "openai-compat",
        "moonshot-v1-32k",
    ),
    (
        "MiMo (Xiaomi)",
        "https://api.xiaomi.com/openai/v1",
        "openai-compat",
        "mimo-7b-rl",
    ),
    (
        "Groq",
        "https://api.groq.com/openai/v1",
        "openai-compat",
        "llama-3.1-70b-versatile",
    ),
    (
        "Together",
        "https://api.together.xyz/v1",
        "openai-compat",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ),
    (
        "Qwen / DashScope",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "openai-compat",
        "qwen2.5-72b-instruct",
    ),
    (
        "SiliconFlow",
        "https://api.siliconflow.cn/v1",
        "openai-compat",
        "Qwen/Qwen2.5-72B-Instruct",
    ),
    ("Ollama (local)", "http://127.0.0.1:11434/v1", "openai-compat", "llama3.1"),
    # LM Studio: the user picks the model in the LM Studio app; we leave
    # a sensible placeholder rather than pretending to know it.
    ("LM Studio (local)", "http://127.0.0.1:1234/v1", "openai-compat", "local-model"),
)


def _seeded_presets() -> list[ProfilePreset]:
    """Materialise the seed set into fresh :class:`ProfilePreset` instances.

    Returns a new list each call so a caller mutating the result can't
    drift the module-level tuple. The order matches the design doc's
    table — Anthropic first, OpenAI second, then alphabetised vendors —
    so the chip row reads predictably out of the box.
    """
    return [
        ProfilePreset(name=name, base_url=base_url, protocol=protocol, model_id=model_id)
        for name, base_url, protocol, model_id in _SEEDED_PRESETS
    ]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _presets_path(settings: Settings) -> Path:
    """Resolve ``<project_root>/.aitap/profile-presets.json``.

    Lives next to ``config.yaml`` so a user inspecting the project's
    ``.aitap/`` directory sees both files together — the design doc
    explicitly anchors presets in the project tree (not ``~``) because
    they're project-relevant metadata (a corporate gateway base_url, a
    chosen vendor preset) the user may want to commit alongside the
    repo's other ``.aitap/`` artefacts.
    """
    return settings.project_root / settings.aitap_dir / "profile-presets.json"


# ---------------------------------------------------------------------------
# Load / save / reset
# ---------------------------------------------------------------------------


def load_presets(settings: Settings) -> list[ProfilePreset]:
    """Read the user's preset list, seeding on first launch.

    Behaviour by state:

    - File missing and ``.aitap/`` exists → seed with the 11 starter
      rows, persist them, and return the seeded list. This is the
      "first launch" path the design doc commits to.
    - File missing and ``.aitap/`` itself missing → return the seeded
      list **without** writing anything. The project was never
      ``aitap init``-ed; creating files outside the user's intent
      would surprise them. The in-memory list is still useful for the
      Add Profile chip row.
    - File present and valid → parse + return. Hand-edits show up
      immediately on the next load.
    - File present but corrupted → log a WARNING, return the seeded
      list (defence in depth: a busted preset file must not break the
      Settings page).

    The save side never silently overwrites a hand-edit beyond the
    documented "PUT /api/profile-presets writes the whole list" path,
    so concurrent edits between the UI and an editor session don't
    clobber each other implicitly.
    """
    path = _presets_path(settings)

    if not path.is_file():
        if not path.parent.is_dir():
            # No .aitap/ at all — the user hasn't initialised the
            # project. Return the seed without writing; the UI still
            # gets a chip row to show.
            _LOGGER.info(
                "No %s and no .aitap/ — using seeded preset list in memory",
                path,
            )
            return _seeded_presets()

        # First launch in an initialised project — seed + persist.
        seeded = _seeded_presets()
        if save_presets(settings, seeded):
            _LOGGER.info("Seeded %s with the 11 starter presets", path)
        return seeded

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        # Corrupted on-disk file — fall back to the seeded list so the
        # Settings page keeps working. We do NOT overwrite the file:
        # the user may have meant to edit it and a half-saved state
        # shouldn't get clobbered. Surface the failure via the log.
        _LOGGER.warning(
            "Couldn't parse %s; falling back to seeded preset list",
            path,
            exc_info=True,
        )
        return _seeded_presets()

    if not isinstance(data, list):
        # Wrong top-level shape (e.g. someone wrote `{}` by hand).
        # Same fallback as the parse-error path.
        _LOGGER.warning(
            "Expected a JSON list in %s; falling back to seeded preset list",
            path,
        )
        return _seeded_presets()

    presets: list[ProfilePreset] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            presets.append(ProfilePreset.model_validate(entry))
        except Exception:
            # A malformed row shouldn't kill the whole list — drop it
            # and log. The user sees the surviving rows in the editor;
            # the failed one is rebuildable from scratch.
            _LOGGER.warning("Skipping malformed preset entry in %s: %r", path, entry)
    return presets


def save_presets(settings: Settings, presets: list[ProfilePreset]) -> bool:
    """Persist *presets* to the on-disk file. Returns ``True`` on success.

    The file is written even when the input list is empty — that's the
    documented "the user explicitly cleared every preset" state, not a
    fallback-to-seed signal. To get the seed back the user clicks
    Reset (which calls :func:`reset_presets`).

    Uses ``json.dump`` with ``indent=2`` so the file reads cleanly when
    hand-edited; ``ensure_ascii=False`` so a non-ASCII chip name (e.g.
    a Chinese label for a private gateway) stays human-readable.
    """
    path = _presets_path(settings)

    if not path.parent.is_dir():
        # No .aitap/ — the project wasn't initialised. We refuse to
        # create the parent for the same reason :func:`load_presets`
        # refuses to seed when ``.aitap/`` is absent.
        _LOGGER.info(
            "No %s — preset changes kept in memory only",
            path.parent,
        )
        return False

    try:
        serialised = json.dumps(
            [p.model_dump(mode="json") for p in presets],
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        path.write_text(serialised + "\n", encoding="utf-8")
    except OSError:
        _LOGGER.warning(
            "Couldn't write %s; preset changes kept in memory only",
            path,
            exc_info=True,
        )
        return False
    return True


def reset_presets(settings: Settings) -> list[ProfilePreset]:
    """Restore the seeded preset list, overwriting any user edits.

    Idempotent: calling twice in a row produces the same on-disk file.
    The function returns the freshly-seeded list so callers don't need a
    second :func:`load_presets` round-trip. If the file lives on a
    read-only filesystem the in-memory result is still correct; the
    Settings page logs the write failure but keeps rendering.
    """
    seeded = _seeded_presets()
    path = _presets_path(settings)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            # Couldn't delete (permissions, race) — best-effort.
            # ``save_presets`` below will still overwrite the content
            # if writes are permitted.
            _LOGGER.warning(
                "Couldn't remove %s during reset; trying to overwrite in place",
                path,
                exc_info=True,
            )
    save_presets(settings, seeded)
    return seeded


__all__ = [
    "load_presets",
    "reset_presets",
    "save_presets",
]
