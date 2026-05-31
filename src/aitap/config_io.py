"""YAML round-trip helpers for the profile-list + defaults blocks.

The legacy ``provider:`` block in ``.aitap/config.yaml`` is handled by
``server/routes/settings.py::_persist_provider_defaults_to_yaml`` —
this module is the parallel implementation for the new ``profiles:``
and ``defaults:`` blocks introduced by the multi-provider redesign.

Design contract:

- **Non-destructive.** Every helper loads the existing YAML, mutates
  only the section it owns (``profiles:`` or ``defaults:``), and
  writes back. The legacy ``provider:`` block is preserved
  byte-for-byte by the round-trip (PyYAML doesn't keep comments but it
  keeps any other top-level keys we didn't touch).
- **Safe-fail.** No ``config.yaml`` → silent skip (the in-memory
  override still wins for the current process). I/O or YAML errors →
  ``WARNING`` log + skip; the API stays alive on a corrupted file.
- **Pure-data interface.** Callers pass lists/objects, not Settings
  instances, so the helpers can be exercised in isolation by tests
  and by future CLI hooks (e.g. ``aitap config show``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import yaml

from aitap.config import DefaultsConfig, ProfileConfig, Settings

_LOGGER = logging.getLogger(__name__)


def _config_path(settings: Settings) -> Path:
    """Resolve ``.aitap/config.yaml`` under the settings' project root."""
    return settings.project_root / settings.aitap_dir / "config.yaml"


def _load_yaml(path: Path) -> dict[str, object] | None:
    """Read ``path`` as YAML mapping; return ``None`` if unreadable.

    The ``None`` return is the signal to "skip persistence" — the
    caller logs and stays in memory-only mode. We don't raise: the
    settings page must remain responsive even when the on-disk config
    is broken.
    """
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError):
        _LOGGER.warning(
            "Couldn't read %s; profile changes kept in memory only",
            path,
            exc_info=True,
        )
        return None
    return loaded if isinstance(loaded, dict) else {}


def _write_yaml(path: Path, data: dict[str, object]) -> bool:
    """Persist ``data`` to ``path``. Returns ``True`` on success.

    A return of ``False`` mirrors the load-side ``None``: the caller
    has already done the in-memory part, so a write failure is
    surfaced as a log warning rather than a 500.
    """
    try:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError:
        _LOGGER.warning(
            "Couldn't write %s; profile changes kept in memory only",
            path,
            exc_info=True,
        )
        return False
    return True


def load_profiles_from_yaml(
    settings: Settings,
) -> tuple[list[ProfileConfig], DefaultsConfig]:
    """Read ``profiles:`` + ``defaults:`` from ``.aitap/config.yaml``.

    Returns the parsed lists/defaults — or empty values when the file
    is missing or corrupted. Never raises; the API consuming this
    helper degrades to "no profiles yet" rather than 500-ing.
    """
    path = _config_path(settings)
    data = _load_yaml(path)
    if data is None:
        return [], DefaultsConfig()

    profiles: list[ProfileConfig] = []
    raw_profiles = data.get("profiles")
    if isinstance(raw_profiles, list):
        for entry in cast(list[object], raw_profiles):
            if not isinstance(entry, dict):
                continue
            try:
                profiles.append(ProfileConfig.model_validate(entry))
            except Exception:
                # A malformed row shouldn't kill the whole settings
                # page — drop the bad entry and log so the user sees it
                # in `aitap doctor` later (when we add that).
                _LOGGER.warning(
                    "Skipping malformed profile entry in %s: %r",
                    path,
                    entry,
                )

    defaults = DefaultsConfig()
    raw_defaults = data.get("defaults")
    if isinstance(raw_defaults, dict):
        try:
            defaults = DefaultsConfig.model_validate(raw_defaults)
        except Exception:
            _LOGGER.warning(
                "Skipping malformed defaults block in %s: %r",
                path,
                raw_defaults,
            )

    return profiles, defaults


def save_profiles_to_yaml(
    settings: Settings,
    profiles: list[ProfileConfig],
    defaults: DefaultsConfig,
) -> bool:
    """Persist ``profiles`` + ``defaults`` back to ``.aitap/config.yaml``.

    Returns ``True`` when the file was written, ``False`` when it was
    skipped (missing file, I/O error, or corrupted-load fallback). The
    legacy ``provider:`` block — and any other top-level keys we don't
    own — are preserved by reading the existing data and only
    overwriting the two sections this module manages.
    """
    path = _config_path(settings)
    if not path.is_file():
        _LOGGER.info(
            "No %s — profile changes kept in memory only",
            path,
        )
        return False

    data = _load_yaml(path)
    if data is None:
        # Couldn't read the existing file; refuse to clobber it from
        # scratch — the user's other config keys deserve preservation.
        # An empty dict here would silently nuke the ``provider:`` block.
        return False

    data["profiles"] = [p.model_dump(mode="json") for p in profiles]
    data["defaults"] = defaults.model_dump(mode="json")
    return _write_yaml(path, data)


def save_defaults_to_yaml(settings: Settings, defaults: DefaultsConfig) -> bool:
    """Persist only the ``defaults:`` block. Convenience wrapper.

    Lets ``PUT /api/settings/defaults`` skip rewriting the whole
    profile list when only the two-id selection changed. Same
    preserve-other-keys behaviour as :func:`save_profiles_to_yaml`.
    """
    path = _config_path(settings)
    if not path.is_file():
        _LOGGER.info(
            "No %s — defaults change kept in memory only",
            path,
        )
        return False

    data = _load_yaml(path)
    if data is None:
        return False

    data["defaults"] = defaults.model_dump(mode="json")
    return _write_yaml(path, data)


__all__ = [
    "load_profiles_from_yaml",
    "save_defaults_to_yaml",
    "save_profiles_to_yaml",
]
