"""Smoke test for ``examples/starter`` — make sure the dogfooding example
keeps running end-to-end with the bundled mock clients."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTER_DIR = REPO_ROOT / "examples" / "starter"


@pytest.fixture
def starter_on_syspath(monkeypatch: pytest.MonkeyPatch):
    """Put ``examples/starter/`` on ``sys.path`` for the duration of one test.

    ``monkeypatch.syspath_prepend`` rolls back the path change automatically;
    we additionally drop any ``starter_app*`` entries from ``sys.modules`` so
    the test is reentrant under ``pytest-xdist`` and back-to-back invocations.
    """
    monkeypatch.syspath_prepend(str(STARTER_DIR))
    yield
    for name in [
        m for m in list(sys.modules) if m == "starter_app" or m.startswith("starter_app.")
    ]:
        del sys.modules[name]


def test_starter_main_runs_end_to_end(starter_on_syspath: None) -> None:
    """Running ``main.run_pipeline`` should produce both stages of output."""
    main_path = STARTER_DIR / "main.py"
    assert main_path.exists(), "examples/starter/main.py is missing"

    import importlib.util

    spec = importlib.util.spec_from_file_location("_starter_main", main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run_pipeline("Hello, please reschedule our meeting.")

    assert set(result) == {"summary", "critique"}
    assert isinstance(result["summary"], str) and result["summary"]
    assert isinstance(result["critique"], str) and result["critique"]


def test_starter_modules_importable(starter_on_syspath: None) -> None:
    """``starter_app`` and its submodules should import cleanly."""
    import starter_app
    from starter_app import anthropic_critic, openai_summarizer
    from starter_app.mocks import FakeAnthropic, FakeOpenAI

    assert callable(starter_app.summarize_email)
    assert callable(starter_app.critique_summary)
    assert callable(openai_summarizer.summarize_email)
    assert callable(anthropic_critic.critique_summary)

    summary = openai_summarizer.summarize_email(FakeOpenAI(), "body")
    critique = anthropic_critic.critique_summary(FakeAnthropic(), summary)
    assert summary
    assert critique
