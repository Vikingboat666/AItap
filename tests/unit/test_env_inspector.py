"""Tests for :mod:`aitap.scanner.rules.env_inspector`."""

from __future__ import annotations

from pathlib import Path

from aitap.scanner.models import Provider
from aitap.scanner.rules.env_inspector import (
    is_config_file,
    is_env_file,
    scan_config_file,
    scan_env_file,
    scan_paths_for_providers,
)


def test_is_env_file_recognises_dotenv_variants() -> None:
    assert is_env_file(Path(".env"))
    assert is_env_file(Path(".env.local"))
    assert is_env_file(Path(".env.example"))
    assert not is_env_file(Path("config.yaml"))


def test_is_config_file_recognises_yaml_and_python() -> None:
    assert is_config_file(Path("config.yaml"))
    assert is_config_file(Path("settings.yml"))
    assert is_config_file(Path("config.py"))
    assert not is_config_file(Path("requirements.txt"))


def test_scan_env_file_detects_known_providers(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# secrets — values not real\n"
        "OPENAI_API_KEY=sk-fake\n"
        "ANTHROPIC_API_KEY=ant-fake\n"
        "DASHSCOPE_API_KEY=ds-fake\n"
        "UNRELATED_VAR=something\n",
        encoding="utf-8",
    )
    evidence = scan_env_file(env, tmp_path)
    providers = {ev.provider for ev in evidence}
    assert providers == {Provider.OPENAI, Provider.ANTHROPIC, Provider.DASHSCOPE}
    for ev in evidence:
        assert ev.source == ".env"
        assert ev.location.line_start >= 1
        assert ev.key_var_name.endswith("_API_KEY")


def test_scan_env_file_does_not_record_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    secret = "sk-this-must-never-be-stored"
    env.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    evidence = scan_env_file(env, tmp_path)
    for ev in evidence:
        assert secret not in ev.model_dump_json()


def test_scan_config_file_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider: openai\nOPENAI_API_KEY: env\nANTHROPIC_API_KEY: env\n",
        encoding="utf-8",
    )
    evidence = scan_config_file(cfg, tmp_path)
    providers = {ev.provider for ev in evidence}
    assert providers == {Provider.OPENAI, Provider.ANTHROPIC}
    for ev in evidence:
        assert ev.source == "config"


def test_scan_paths_dispatches_correctly(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ANTHROPIC_API_KEY: y\n", encoding="utf-8")
    other = tmp_path / "README.md"
    other.write_text("# nothing to see here\n", encoding="utf-8")

    evidence = scan_paths_for_providers([env, cfg, other], tmp_path)
    sources = {ev.source for ev in evidence}
    assert sources == {".env", "config"}


def test_scan_env_file_dedupes_repeated_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=a\nOPENAI_API_KEY=b\n", encoding="utf-8")
    evidence = scan_env_file(env, tmp_path)
    assert len(evidence) == 1
