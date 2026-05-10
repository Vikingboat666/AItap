"""Tests for :mod:`aitap.scanner.languages.python`."""

from __future__ import annotations

from pathlib import Path

from aitap.scanner.languages.python import scan_python_file
from aitap.scanner.models import Confidence, Provider, Role, TemplateKind


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_scan_finds_openai_chat_call(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "a.py",
        """\
from openai import OpenAI

client = OpenAI()


def summarize(body: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.1,
        max_tokens=80,
        messages=[
            {"role": "system", "content": "You summarise."},
            {"role": "user", "content": f"Summarise: {body}"},
        ],
    )
    return response.choices[0].message.content
""",
    )
    sites, warnings = scan_python_file(src, tmp_path)
    assert warnings == []
    assert len(sites) == 1
    site = sites[0]
    assert site.provider is Provider.OPENAI
    assert site.confidence is Confidence.HIGH
    assert site.parameters.model == "gpt-4o"
    assert site.parameters.temperature == 0.1
    assert site.parameters.max_tokens == 80
    assert [m.role for m in site.messages] == [Role.SYSTEM, Role.USER]
    assert site.messages[1].template_kind is TemplateKind.FSTRING
    assert site.location.file == "a.py"
    assert site.name == "summarize"


def test_scan_finds_anthropic_with_separate_system(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "agent.py",
        """\
from anthropic import Anthropic

client = Anthropic()


def ask(question: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system="You are a careful researcher.",
        messages=[{"role": "user", "content": question}],
    )
    return response.content[0].text
""",
    )
    sites, warnings = scan_python_file(src, tmp_path)
    assert warnings == []
    assert len(sites) == 1
    messages = sites[0].messages
    assert [m.role for m in messages] == [Role.SYSTEM, Role.USER]
    assert messages[0].template_text == "You are a careful researcher."


def test_scan_skips_unrelated_calls(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "noop.py",
        """\
import logging
logging.getLogger().info("hello %s", "world")
print("nothing here")
""",
    )
    sites, warnings = scan_python_file(src, tmp_path)
    assert sites == []
    assert warnings == []


def test_scan_unparseable_file_falls_back_to_tree_sitter(tmp_path: Path) -> None:
    """A syntactically invalid file should still surface a candidate via
    tree-sitter, with a W001 warning attached."""
    src = _write(
        tmp_path,
        "broken.py",
        """\
def broken(:
    client.messages.create(model="m", messages=[{"role": "user", "content": "hi"}])
""",
    )
    sites, warnings = scan_python_file(src, tmp_path)
    assert any(w.code == "W001-unparseable" for w in warnings)
    # Tree-sitter is forgiving — we expect at least one low-confidence site.
    assert any(site.confidence is Confidence.LOW for site in sites)


def test_scan_records_unresolved_when_messages_built_dynamically(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "dynamic.py",
        """\
from openai import OpenAI
client = OpenAI()


def call(messages):
    return client.chat.completions.create(model="m", messages=messages)
""",
    )
    sites, _ = scan_python_file(src, tmp_path)
    assert len(sites) == 1
    site = sites[0]
    assert site.confidence is Confidence.MEDIUM
    assert site.messages[0].template_kind is TemplateKind.UNRESOLVED


def test_scan_extracts_legacy_completion_prompt(tmp_path: Path) -> None:
    src = _write(
        tmp_path,
        "legacy.py",
        """\
from openai import OpenAI
client = OpenAI()


def write(name):
    return client.completions.create(
        model="gpt-3.5-turbo-instruct",
        max_tokens=50,
        prompt=f"Hi {name}",
    )
""",
    )
    sites, _ = scan_python_file(src, tmp_path)
    assert len(sites) == 1
    msg = sites[0].messages[0]
    assert msg.role is Role.USER
    assert msg.template_kind is TemplateKind.FSTRING
    assert msg.template_text == "Hi {name}"


def test_site_id_is_stable_for_same_input(tmp_path: Path) -> None:
    body = """\
from openai import OpenAI
client = OpenAI()
client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
"""
    p1 = _write(tmp_path, "a.py", body)
    sites1, _ = scan_python_file(p1, tmp_path)
    p2 = _write(tmp_path, "a.py", body)
    sites2, _ = scan_python_file(p2, tmp_path)
    assert sites1[0].id == sites2[0].id


def test_two_calls_on_same_line_get_distinct_ids(tmp_path: Path) -> None:
    """Same-line nested / list-comprehension calls must produce distinct ids
    — the col_start in the fingerprint disambiguates them."""
    src = _write(
        tmp_path,
        "twin.py",
        """\
from openai import OpenAI
client = OpenAI()
def call(x): return [client.chat.completions.create(model="m", messages=[{"role":"user","content":"a"}]), client.chat.completions.create(model="m", messages=[{"role":"user","content":"b"}])]
""",
    )
    sites, _ = scan_python_file(src, tmp_path)
    assert len(sites) == 2
    assert sites[0].id != sites[1].id


def test_metrics_completions_create_is_not_a_false_positive(tmp_path: Path) -> None:
    """Headline regression for review #1: a file without 'import openai' that
    happens to call something like `metrics.completions.create(...)` should
    NOT produce a prompt site."""
    src = _write(
        tmp_path,
        "metrics.py",
        """\
import statsd
metrics = statsd.StatsClient()


def emit():
    metrics.completions.create(prompt="signup")
""",
    )
    sites, warnings = scan_python_file(src, tmp_path)
    assert sites == []
    assert warnings == []
