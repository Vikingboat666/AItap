"""CLI surface tests.

Covers:
    * `aitap --help` lists every advertised subcommand.
    * `aitap --version` prints the package version.
    * `aitap init` creates the full .aitap/ skeleton in an empty directory,
      reports tri-state status (created/appended/exists), is idempotent on
      re-run, respects --force, and refuses to operate on non-existent paths.
    * Stub commands (scan/audit/ui/diff/rollback) accept their documented
      flags, exit cleanly, and surface "not yet implemented" notices on stderr.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aitap import __version__
from aitap.cli import _GITIGNORE_BANNER, _GITIGNORE_ENTRIES, app

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _normalize(s: str) -> str:
    """Strip ANSI escapes and collapse whitespace.

    Typer's rich-rendered --help wraps long lines and emits ANSI styling
    that varies with CI terminal width and color detection. For substring
    assertions we don't care about layout, only that the flag name is
    present somewhere in the output.
    """
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", s))


@pytest.fixture()
def runner() -> CliRunner:
    # Click 8.3+ keeps stdout/stderr separate by default — `result.stderr`
    # captures the rich-styled "not yet implemented" notices that the stub
    # commands deliberately route off stdout.
    #
    # COLUMNS=200 widens rich-rendered typer --help to avoid most wrapping;
    # _normalize() is the belt-and-suspenders for whatever wrapping is left.
    return CliRunner(env={"COLUMNS": "200"})


# --------------------------------------------------------------------------- #
# top-level                                                                   #
# --------------------------------------------------------------------------- #


def test_root_help_lists_all_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    clean = _normalize(result.stdout)
    for name in ("init", "scan", "audit", "ui", "diff", "rollback"):
        assert name in clean, f"missing subcommand in --help: {name}"


def test_no_args_shows_help(runner: CliRunner) -> None:
    # `no_args_is_help=True` on the Typer app — bare invocation should print help
    # and exit non-zero (Typer convention for "user gave no command").
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Usage" in result.stdout or "Usage" in result.stderr


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert __version__ in result.stdout


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


def test_init_creates_skeleton(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output

    aitap_dir = tmp_path / ".aitap"
    assert aitap_dir.is_dir()
    for sub in ("prompts", "pipelines", "datasets", "runs"):
        assert (aitap_dir / sub).is_dir(), f".aitap/{sub}/ was not created"

    config_path = aitap_dir / "config.yaml"
    assert config_path.is_file()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["provider"]["name"] == "anthropic"
    assert "per_run_usd" in config["cost"]

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert _GITIGNORE_BANNER in gitignore
    assert ".aitap/db.sqlite" in gitignore
    assert ".aitap/db.sqlite-*" in gitignore  # WAL companions covered by glob
    assert ".aitap/runs/" in gitignore

    # Confirm the report panel actually rendered to stdout — guards against
    # rich panels being clipped at narrow terminal widths in CI.
    assert "aitap init" in result.stdout
    assert "created" in result.stdout


def test_init_default_config_uses_provider_defaults(runner: CliRunner, tmp_path: Path) -> None:
    """The default config.yaml is built from config.py's pydantic defaults."""
    from aitap.config import CostLimits, ProviderConfig

    runner.invoke(app, ["init", str(tmp_path)])
    config = yaml.safe_load((tmp_path / ".aitap" / "config.yaml").read_text(encoding="utf-8"))

    assert config["provider"]["name"] == ProviderConfig().name
    assert config["provider"]["model"] == ProviderConfig().model
    assert config["cost"]["per_run_usd"] == CostLimits().per_run_usd


def test_init_appends_to_existing_gitignore(runner: CliRunner, tmp_path: Path) -> None:
    existing = "node_modules/\n*.log\n"
    (tmp_path / ".gitignore").write_text(existing, encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output

    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # User's existing entries are preserved verbatim
    assert gi.startswith(existing)
    assert _GITIGNORE_BANNER in gi
    # Status table reports "appended", not "created" — guards against the
    # original review's #1: misreporting an edit as a fresh write.
    assert "appended" in result.stdout
    assert "created .gitignore" not in result.stdout.lower()


def test_init_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    first = runner.invoke(app, ["init", str(tmp_path)])
    assert first.exit_code == 0, first.output

    config_path = tmp_path / ".aitap" / "config.yaml"
    config_path.write_text("provider:\n  name: openai\n", encoding="utf-8")

    second = runner.invoke(app, ["init", str(tmp_path)])
    assert second.exit_code == 0, second.output

    # Second run preserves user edits when --force is absent.
    assert "openai" in config_path.read_text(encoding="utf-8")
    assert "exists" in second.stdout

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Block appears exactly once even after re-running init
    assert gitignore.count(_GITIGNORE_BANNER) == 1


def test_init_force_overwrites_config(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    config_path = tmp_path / ".aitap" / "config.yaml"
    config_path.write_text("provider:\n  name: openai\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0, result.output

    # --force restores the default template
    assert "anthropic" in config_path.read_text(encoding="utf-8")


def test_init_rejects_nonexistent_path(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist"
    result = runner.invoke(app, ["init", str(target)])
    # Non-existent paths must error rather than silently mkdir — typer's
    # dir_okay only validates type when the path exists, so we layer our
    # own check on top.
    assert result.exit_code == 2
    # Rich may wrap the message across lines on long Windows tmp paths;
    # strip whitespace before matching the substring.
    assert "does not exist" in " ".join(result.stderr.split())
    assert not target.exists()


def test_init_rejects_file_path(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "regular_file"
    target.write_text("hi", encoding="utf-8")
    result = runner.invoke(app, ["init", str(target)])
    # typer's `file_okay=False` catches existing files before our handler runs.
    assert result.exit_code != 0


def test_init_help_documents_force(runner: CliRunner) -> None:
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0, result.output
    assert "--force" in _normalize(result.stdout)


def test_gitignore_entries_cover_sqlite_companions() -> None:
    """The block must cover WAL (-wal/-shm) and rollback journal (-journal)."""
    assert ".aitap/db.sqlite-*" in _GITIGNORE_ENTRIES, (
        "Glob entry is required so we don't leak WAL companion files."
    )
    # Per review #4, audit-cache is owned by wt/audit and should not be
    # pre-declared by cli-scaffold.
    assert not any("audit-cache" in entry for entry in _GITIGNORE_ENTRIES)


# --------------------------------------------------------------------------- #
# scan stub                                                                   #
# --------------------------------------------------------------------------- #


def test_scan_help_shows_flags(runner: CliRunner) -> None:
    result = runner.invoke(app, ["scan", "--help"])
    assert result.exit_code == 0, result.output
    clean = _normalize(result.stdout)
    assert "--rules-only" in clean
    assert "--deep" in clean


def test_scan_runs_against_empty_dir(runner: CliRunner, tmp_path: Path) -> None:
    """Sanity check that we registered scanner's scan_command — it should
    exit cleanly on an empty directory. cli-scaffold is not responsible for
    the scanner's behavior; this is just integration smoke-testing the wiring.
    """
    result = runner.invoke(app, ["scan", str(tmp_path), "--rules-only"])
    assert result.exit_code == 0, result.output


def test_scan_rejects_conflicting_flags(runner: CliRunner, tmp_path: Path) -> None:
    # scanner's scan_command raises typer.BadParameter, which Click renders
    # as a usage error (exit 2) with the message in stderr.
    result = runner.invoke(app, ["scan", str(tmp_path), "--rules-only", "--deep"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr


# --------------------------------------------------------------------------- #
# audit / ui / diff / rollback stubs                                          #
# --------------------------------------------------------------------------- #


def test_audit_help_documents_repo_arg(runner: CliRunner) -> None:
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0, result.output
    assert "gh:owner/repo" in _normalize(result.stdout)


def test_audit_stub_runs(runner: CliRunner) -> None:
    result = runner.invoke(app, ["audit", "gh:foo/bar"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.stderr


def test_ui_help_shows_port(runner: CliRunner) -> None:
    result = runner.invoke(app, ["ui", "--help"])
    assert result.exit_code == 0, result.output
    assert "--port" in _normalize(result.stdout)


def test_ui_help_does_not_advertise_public_bind(runner: CliRunner) -> None:
    """Per review #7: don't tempt users to bind 0.0.0.0 in --host help text."""
    result = runner.invoke(app, ["ui", "--help"])
    assert "0.0.0.0" not in _normalize(result.stdout)


def test_ui_stub_runs(runner: CliRunner) -> None:
    result = runner.invoke(app, ["ui", "--port", "9001", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.stderr


def test_diff_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["diff", "--help"])
    assert result.exit_code == 0, result.output
    clean = _normalize(result.stdout)
    # All three positionals appear in the usage line
    for arg in ("PROMPT", "V1", "V2"):
        assert arg in clean


def test_diff_stub_runs(runner: CliRunner) -> None:
    result = runner.invoke(app, ["diff", "summarize_email", "1", "3"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.stderr


def test_rollback_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["rollback", "--help"])
    assert result.exit_code == 0, result.output
    clean = _normalize(result.stdout)
    assert "PROMPT" in clean
    assert "VERSION" in clean


def test_rollback_stub_runs(runner: CliRunner) -> None:
    result = runner.invoke(app, ["rollback", "summarize_email", "2", "--yes"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.stderr


def test_stub_does_not_swallow_real_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per review #10: present-but-broken modules must raise, not be misreported.

    Simulates a downstream worktree (here: wt/audit) merging a module whose
    import body crashes. A pre-fix `try/except ImportError` would have
    swallowed this and shown "not yet implemented"; with the
    `_module_available` probe + explicit import, the error propagates.

    Monkeypatches `_module_available` and the top-level `import_module`
    binding directly — robust to whether cli.py uses `from importlib import
    import_module` or `importlib.import_module(...)`.
    """
    import sys

    def fake_import_module(name: str) -> object:
        if name == "aitap.audit.clone":
            raise RuntimeError("boom from broken downstream module")
        from importlib import import_module as real_import_module

        return real_import_module(name)

    monkeypatch.setattr("aitap.cli._module_available", lambda _name: True)
    monkeypatch.setattr("aitap.cli.import_module", fake_import_module)

    # Ensure no leftover module from prior tests
    sys.modules.pop("aitap.audit.clone", None)

    runner = CliRunner()
    result = runner.invoke(app, ["audit", "gh:foo/bar"])
    # The real bug surfaces: non-zero exit, the RuntimeError reaches the caller
    # rather than being silently misreported as "not yet implemented".
    assert result.exit_code != 0
    assert "not yet implemented" not in (result.stderr or "")
    assert isinstance(result.exception, RuntimeError)


# --------------------------------------------------------------------------- #
# UTF-8 stdio guard (Windows GBK crash regression)                            #
# --------------------------------------------------------------------------- #


class _FakeReconfigurableStream:
    """Stand-in for sys.stdout exposing the same `encoding` + `reconfigure`
    surface that Python's TextIOWrapper provides on real terminals.
    """

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.reconfigure_calls: list[dict[str, object]] = []

    def reconfigure(self, **kwargs: object) -> None:
        self.reconfigure_calls.append(kwargs)
        if "encoding" in kwargs:
            self.encoding = str(kwargs["encoding"])


def test_force_utf8_stdio_reconfigures_non_utf8_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aitap.cli import _force_utf8_stdio

    fake_out = _FakeReconfigurableStream("cp936")
    fake_err = _FakeReconfigurableStream("cp1252")
    monkeypatch.setattr("aitap.cli.sys.stdout", fake_out)
    monkeypatch.setattr("aitap.cli.sys.stderr", fake_err)

    _force_utf8_stdio()

    assert fake_out.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert fake_err.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]


@pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", "utf8", "UTF8"])
def test_force_utf8_stdio_skips_already_utf8(
    monkeypatch: pytest.MonkeyPatch, encoding: str
) -> None:
    from aitap.cli import _force_utf8_stdio

    fake = _FakeReconfigurableStream(encoding)
    monkeypatch.setattr("aitap.cli.sys.stdout", fake)
    monkeypatch.setattr("aitap.cli.sys.stderr", fake)

    _force_utf8_stdio()

    # Already-utf8 streams should be left alone — we don't churn the encoding.
    assert fake.reconfigure_calls == []


def test_force_utf8_stdio_tolerates_streams_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StringIO et al. lack ``reconfigure``; the helper must not crash."""
    from io import StringIO

    from aitap.cli import _force_utf8_stdio

    monkeypatch.setattr("aitap.cli.sys.stdout", StringIO())
    monkeypatch.setattr("aitap.cli.sys.stderr", StringIO())

    _force_utf8_stdio()  # must not raise


def test_force_utf8_stdio_swallows_reconfigure_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a stream's reconfigure() raises (e.g. binary mode), keep going."""

    class _BrokenStream:
        encoding = "cp936"

        def reconfigure(self, **_kwargs: object) -> None:
            raise OSError("cannot reconfigure binary stream")

    from aitap.cli import _force_utf8_stdio

    monkeypatch.setattr("aitap.cli.sys.stdout", _BrokenStream())
    monkeypatch.setattr("aitap.cli.sys.stderr", _BrokenStream())

    _force_utf8_stdio()  # must not raise


def test_scan_renders_unicode_glyphs_into_non_utf8_pipe(tmp_path: Path) -> None:
    """End-to-end regression: aitap scan must not crash when its stdout is a
    pipe whose underlying buffer would default to a Unicode-hostile codec
    (the original Windows GBK crash). We simulate this by capturing through
    a buffer and asserting the bullet glyph survived to the output.
    """
    from aitap.cli import app

    # Use the real openai_basic fixture — it produces bullets, arrows, and
    # ellipses through rich's Markdown renderer.
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "openai_basic"
    runner = CliRunner(env={"COLUMNS": "120"})
    result = runner.invoke(app, ["scan", str(fixture)])

    assert result.exit_code == 0, (
        f"aitap scan crashed: {result.exception!r}\nstderr:\n{result.stderr}"
    )
    # The bullet character is the canary that originally triggered the
    # UnicodeEncodeError on Windows + GBK. If it makes it to stdout, we know
    # rich could write to the captured stream without exploding.
    assert "•" in result.stdout
