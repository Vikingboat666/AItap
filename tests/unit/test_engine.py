"""Tests for :mod:`aitap.scanner.engine`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aitap.scanner.engine import scan_project
from aitap.scanner.models import Provider

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
OPENAI_BASIC = _FIXTURES / "openai_basic"
ANTHROPIC_AGENT = _FIXTURES / "anthropic_agent"


def test_scan_openai_basic_returns_at_least_two_sites() -> None:
    """Acceptance criterion from WORKTREES.md."""
    result = scan_project(OPENAI_BASIC)
    assert len(result.prompts) >= 2
    assert all(site.provider is Provider.OPENAI for site in result.prompts)


def test_scan_openai_basic_picks_up_env_evidence() -> None:
    result = scan_project(OPENAI_BASIC)
    providers = {ev.provider for ev in result.providers_detected}
    assert Provider.OPENAI in providers


def test_scan_anthropic_agent_finds_messages_and_stream() -> None:
    result = scan_project(ANTHROPIC_AGENT)
    providers = {site.provider for site in result.prompts}
    assert providers == {Provider.ANTHROPIC}
    # messages.create + messages.stream → 2 sites.
    assert len(result.prompts) >= 2
    cfg_evidence = {ev.provider for ev in result.providers_detected}
    assert Provider.ANTHROPIC in cfg_evidence


def test_scan_handles_nonexistent_root(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        scan_project(missing)


def test_scan_skips_default_ignore_dirs(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text(
        "from openai import OpenAI\n"
        'OpenAI().chat.completions.create(model="m",'
        ' messages=[{"role":"user","content":"hi"}])\n',
        encoding="utf-8",
    )
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    cached = cache / "fake.py"
    cached.write_text(
        'OpenAI().chat.completions.create(model="m",'
        ' messages=[{"role":"user","content":"ignored"}])\n',
        encoding="utf-8",
    )
    result = scan_project(tmp_path)
    files = {site.location.file for site in result.prompts}
    assert "real.py" in files
    assert "__pycache__/fake.py" not in files


def test_scan_result_serialises_to_json_round_trip() -> None:
    result = scan_project(OPENAI_BASIC)
    payload = result.model_dump_json()
    parsed = json.loads(payload)
    assert parsed["files_scanned"] >= 1
    assert isinstance(parsed["prompts"], list)


def _run_module(*args: str) -> subprocess.CompletedProcess[str]:
    """Run `python -m aitap.scanner.engine ...` with UTF-8 stdio so the rich
    Markdown renderer's bullet glyphs survive the trip through the Windows
    console codepage during local test runs."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "aitap.scanner.engine", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_module_main_emits_json() -> None:
    """Run `python -m aitap.scanner.engine <fixture> --json` end-to-end."""
    proc = _run_module(str(OPENAI_BASIC), "--json")
    parsed = json.loads(proc.stdout)
    assert parsed["files_scanned"] >= 1
    assert len(parsed["prompts"]) >= 2


def test_module_main_renders_markdown_when_no_json() -> None:
    proc = _run_module(str(OPENAI_BASIC))
    assert "aitap scan" in proc.stdout
    assert "Prompts" in proc.stdout


def test_module_main_emits_no_runtime_warnings() -> None:
    """Running with -W error::RuntimeWarning catches the runpy double-import
    warning that was present before scanner/__init__.py was made lazy."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::RuntimeWarning",
            "-m",
            "aitap.scanner.engine",
            str(OPENAI_BASIC),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, (
        f"runpy raised RuntimeWarning under -W error\nSTDERR:\n{proc.stderr}"
    )
