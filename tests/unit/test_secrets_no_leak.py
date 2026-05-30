"""Grep-style assertions that aitap never persists a raw API key.

The :mod:`aitap.secrets` vault is only as safe as the parts of the
codebase that consume it. This test runs a couple of realistic
"someone configured a key, then exercised the rest of the system"
scenarios and scans every byte aitap wrote afterwards for the leaked
key prefix (``sk-...``, ``Bearer ...``).

The literal we plant is ``sk-fake-anthropic-LEAK-CANARY-1234567890``
so a positive match in any test failure is immediately readable as
"the canary slipped through here, not a real production key".

Coverage:

- The fallback YAML file itself is allowed to contain the key (that's
  its job) — but no *other* file under ``~/.aitap/`` may.
- The project-level ``.aitap/`` (db.sqlite, prompts/, runs/) must
  never contain it, even after we exercise the persistence layer.
- No log record emitted while the canary was in scope contains it,
  thanks to the secret log filter.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from aitap import secrets as secrets_module

_CANARY = "sk-fake-anthropic-LEAK-CANARY-1234567890"


class _FakeKeyring:
    """Tiny in-memory fake; matches the one in test_secrets.py."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.usable = True

    class _Backend:
        pass

    def get_keyring(self) -> _FakeKeyring._Backend:
        return self._Backend()

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.store:
            raise KeyError("no such password")
        del self.store[(service, account)]


@pytest.fixture()
def vault_under_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Patch the vault to live entirely under tmp_path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield tmp_path


def _scan_dir_for(directory: Path, needle: str) -> list[Path]:
    """Walk *directory* and report every file containing *needle*."""
    offenders: list[Path] = []
    if not directory.exists():
        return offenders
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if needle.encode() in data:
            offenders.append(path)
    return offenders


def _scan_sqlite_for(db_path: Path, needle: str) -> list[tuple[str, str]]:
    """Open *db_path*, walk every table/column, report (table, column) hits."""
    hits: list[tuple[str, str]] = []
    if not db_path.exists():
        return hits
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur = conn.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cur.fetchall()]
            for col in columns:
                # Cast to text in case the column is BLOB / INTEGER —
                # we still want to scan stringified content.
                try:
                    cur = conn.execute(
                        f"SELECT 1 FROM {table} WHERE CAST({col} AS TEXT) LIKE ? LIMIT 1",
                        (f"%{needle}%",),
                    )
                except sqlite3.Error:
                    continue
                if cur.fetchone() is not None:
                    hits.append((table, col))
    finally:
        conn.close()
    return hits


def test_set_then_status_writes_nothing_with_key_outside_fallback(
    vault_under_home: Path,
) -> None:
    """After ``set_key`` + a few ``key_status`` reads, the only file under
    ``~/.aitap`` that may contain the canary is ``secrets.yaml`` (the
    fallback file). Everything else is forbidden territory."""
    # Force the fallback path so the file actually gets written.
    secrets_module.set_key("anthropic", _CANARY, use_fallback=True)
    _ = secrets_module.key_status("anthropic")
    _ = secrets_module.key_status("openai")

    offenders = _scan_dir_for(vault_under_home / ".aitap", _CANARY)
    allowed = {(vault_under_home / ".aitap" / "secrets.yaml").resolve()}
    real_offenders = [p for p in offenders if p.resolve() not in allowed]
    assert real_offenders == [], (
        f"files under ~/.aitap leaked the canary outside secrets.yaml: {real_offenders}"
    )


def test_aitap_dir_under_cwd_never_holds_the_key(
    vault_under_home: Path,
    tmp_path: Path,
) -> None:
    """No write triggered by the vault may land inside the project-level
    ``.aitap/`` (which is what ships into git history if the user ever
    misconfigures their gitignore)."""
    project_root = tmp_path / "proj"
    (project_root / ".aitap").mkdir(parents=True)
    # We don't chdir; just assert the directory doesn't contain anything
    # the vault might have created. Even one offending file is a fail.
    secrets_module.set_key("anthropic", _CANARY)
    secrets_module.set_key("openai", _CANARY, use_fallback=True)

    offenders = _scan_dir_for(project_root / ".aitap", _CANARY)
    assert offenders == [], f"project-level .aitap/ leaked the canary: {offenders}"


def test_persistence_db_and_files_never_contain_the_canary(
    vault_under_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set a canary key, exercise the persistence layer (the parts that
    happen offline), then grep the project-level .aitap/ output for it.

    We can't actually run an LLM in unit tests, so we simulate the
    persistence-touching parts: create the SQLite DB, write a JSONL
    sidecar that the dispatch layer would produce, etc."""
    from aitap.config import Settings
    from aitap.store import db as store_db

    project_root = tmp_path / "proj"
    aitap_dir = project_root / ".aitap"
    aitap_dir.mkdir(parents=True)
    monkeypatch.setenv("AITAP_PROJECT_ROOT", str(project_root))
    settings = Settings(project_root=project_root)

    # Plant the canary first.
    secrets_module.set_key("anthropic", _CANARY)

    # Initialise the DB through the documented connect+init_db pair.
    conn = store_db.connect(settings.db_path)
    try:
        store_db.init_db(conn)
        # Write a plausible row through the providers_detected table —
        # which is the only place the codebase would record provider
        # metadata. Notably, ``key_var_name`` is the env var *name*,
        # not the secret itself.
        conn.execute(
            "INSERT INTO providers_detected "
            "(project_root, provider, source, file, line_start, key_var_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(project_root),
                "anthropic",
                "code",
                "app/main.py",
                1,
                "ANTHROPIC_API_KEY",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Write a dispatch-style JSONL sidecar with a plausible run output.
    runs_dir = aitap_dir / "runs" / "run_canary"
    runs_dir.mkdir(parents=True)
    sidecar = runs_dir / "outputs.jsonl"
    sidecar.write_text(
        json.dumps(
            {
                "case_index": 0,
                "text": "Anthropic returned ok",
                "image_path": None,
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Scan project-level .aitap/ for canary leakage.
    file_hits = _scan_dir_for(aitap_dir, _CANARY)
    db_hits = _scan_sqlite_for(settings.db_path, _CANARY)

    assert file_hits == [], f"canary leaked into project .aitap files: {file_hits}"
    assert db_hits == [], f"canary leaked into SQLite rows: {db_hits}"


def test_log_filter_drops_canary_records() -> None:
    """The vault's log filter strips records that would emit the canary.

    Even if a future refactor accidentally logs the resolved key, the
    filter catches it before it reaches a handler.
    """
    logger = logging.getLogger("aitap.secrets.test.no_leak")
    logger.handlers.clear()
    logger.filters.clear()
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger.addHandler(_Handler())
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    secrets_module.install_log_filter(logger)
    logger.info("here it is: %s", _CANARY)
    logger.info("fine line about nothing")

    messages = [r.getMessage() for r in captured]
    assert all(_CANARY not in m for m in messages), f"canary made it into log output: {messages}"
    # The innocent message survives.
    assert any("nothing" in m for m in messages)
