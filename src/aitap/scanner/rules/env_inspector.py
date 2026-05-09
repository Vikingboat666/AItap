"""Detect which LLM providers a project has configured.

Walks the project root looking for .env-style files, ``config.py``,
``config.yaml``/``settings.yaml`` and reads them for *names* (not values) of
known provider API key variables. Values are never read back — we only want
to know "this repo has wired up Anthropic" without harvesting secrets.

Each match emits a :class:`ProviderEvidence` referencing the file/line where
the key name first appeared.

Patterns detected (extend :data:`PROVIDER_KEY_PATTERNS` to add more):

- ``OPENAI_API_KEY``  → :data:`Provider.OPENAI`
- ``ANTHROPIC_API_KEY`` → :data:`Provider.ANTHROPIC`
- ``LANGCHAIN_API_KEY``, ``LANGSMITH_API_KEY`` → :data:`Provider.LANGCHAIN`
- ``LLAMAINDEX_*`` → :data:`Provider.LLAMAINDEX`
- ``DASHSCOPE_API_KEY`` → :data:`Provider.DASHSCOPE`
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from aitap.scanner.models import CodeLocation, Provider, ProviderEvidence

# Source-tag matching the Literal in :class:`ProviderEvidence.source`. Kept as
# a distinct alias so the dispatch table here and the contract field stay in
# lock-step.
EnvSource = Literal[".env", "config", "code"]


PROVIDER_KEY_PATTERNS: list[tuple[Provider, re.Pattern[str]]] = [
    (Provider.OPENAI, re.compile(r"^(?:OPENAI_API_KEY|OPENAI_ORG(?:_ID)?)$")),
    (Provider.ANTHROPIC, re.compile(r"^ANTHROPIC_API_KEY$")),
    (
        Provider.LANGCHAIN,
        re.compile(r"^(?:LANGCHAIN_API_KEY|LANGSMITH_API_KEY|LANGCHAIN_TRACING_V2)$"),
    ),
    (Provider.LLAMAINDEX, re.compile(r"^LLAMAINDEX_[A-Z0-9_]+$")),
    (Provider.DASHSCOPE, re.compile(r"^DASHSCOPE_API_KEY$")),
]


# Files we treat as ".env" syntax (KEY=value, line-oriented, possibly quoted).
_ENV_FILE_NAMES = {".env", ".env.local", ".env.example", ".env.sample"}
_ENV_SUFFIXES = {".env"}


# Files we treat as YAML-ish config — we just regex for known keys, no parser.
_CONFIG_NAMES = {
    "config.yaml",
    "config.yml",
    "settings.yaml",
    "settings.yml",
}
_CONFIG_PY_NAMES = {"config.py", "settings.py"}


def is_env_file(path: Path) -> bool:
    name = path.name.lower()
    if name in _ENV_FILE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _ENV_SUFFIXES)


def is_config_file(path: Path) -> bool:
    name = path.name.lower()
    return name in _CONFIG_NAMES or name in _CONFIG_PY_NAMES


def scan_env_file(path: Path, project_root: Path) -> list[ProviderEvidence]:
    """Parse a .env-style file and emit a :class:`ProviderEvidence` for each
    line whose key matches a known provider pattern.

    The file's *values* are intentionally ignored.
    """
    source: EnvSource = ".env"
    return list(_scan_keyvalue(path, project_root, source=source))


def scan_config_file(path: Path, project_root: Path) -> list[ProviderEvidence]:
    """Same as :func:`scan_env_file` but for YAML/Python config files.

    Uses regex line-scanning rather than a real parser — we only care whether
    a known-key *name* appears anywhere in the file. This avoids needing to
    interpret arbitrary Python or YAML structures.
    """
    source: EnvSource = "config"
    return list(_scan_keyvalue(path, project_root, source=source))


_KEY_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:export\s+)?              # optional bash 'export'
    ['"]?                        # optional quote (yaml-style)
    (?P<key>[A-Z][A-Z0-9_]+)     # uppercase identifier
    ['"]?
    \s*[:=]                      # = (env) or : (yaml) or = (python)
    """,
    re.VERBOSE,
)


def _scan_keyvalue(
    path: Path, project_root: Path, *, source: EnvSource
) -> Iterable[ProviderEvidence]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    relpath = _project_relative(path, project_root)
    seen: set[tuple[Provider, str]] = set()
    out: list[ProviderEvidence] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _KEY_LINE_RE.match(raw)
        if match is None:
            continue
        key = match.group("key")
        for provider, pattern in PROVIDER_KEY_PATTERNS:
            if pattern.match(key) is None:
                continue
            fingerprint = (provider, key)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(
                ProviderEvidence(
                    provider=provider,
                    source=source,
                    location=CodeLocation(
                        file=relpath,
                        line_start=lineno,
                        line_end=lineno,
                    ),
                    key_var_name=key,
                )
            )
    return out


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def scan_paths_for_providers(paths: Iterable[Path], project_root: Path) -> list[ProviderEvidence]:
    """Scan a flat iterable of candidate paths, dispatching to env/config logic
    per file."""
    out: list[ProviderEvidence] = []
    for path in paths:
        if not path.is_file():
            continue
        if is_env_file(path):
            out.extend(scan_env_file(path, project_root))
        elif is_config_file(path):
            out.extend(scan_config_file(path, project_root))
    return out
