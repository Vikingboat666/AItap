"""Audit-mode tests — exercise URL resolution, lifecycle, and the L2 gate.

We mock ``git.Repo.clone_from`` so unit tests never hit the network. A
real-network smoke test lives at ``tests/integration/`` gated on
``AITAP_RUN_INTEGRATION=1``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from aitap.audit.clone import audit_repo, resolve_repo_url

# --------------------------------------------------------------------------- #
# resolve_repo_url                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("input_url", "expected"),
    [
        ("gh:foo/bar", "https://github.com/foo/bar.git"),
        (
            "gh:owner-with-dashes/repo_with_under",
            "https://github.com/owner-with-dashes/repo_with_under.git",
        ),
        ("https://github.com/foo/bar.git", "https://github.com/foo/bar.git"),
        ("git@github.com:foo/bar.git", "git@github.com:foo/bar.git"),
        ("ssh://git@gitlab.com/foo/bar.git", "ssh://git@gitlab.com/foo/bar.git"),
    ],
)
def test_resolve_repo_url_accepts_supported_forms(input_url: str, expected: str) -> None:
    assert resolve_repo_url(input_url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "gh:",
        "gh:foo",
        "gh:foo/",
        "gh:/bar",
        "gh:foo/bar/baz",  # too many segments
    ],
)
def test_resolve_repo_url_rejects_invalid_gh_shorthand(bad: str) -> None:
    with pytest.raises(typer.BadParameter):
        resolve_repo_url(bad)


# --------------------------------------------------------------------------- #
# audit_repo: L2 gate                                                         #
# --------------------------------------------------------------------------- #


def test_audit_repo_refuses_l2_explicitly() -> None:
    """Even if the caller passes rules_only=False, audit must refuse to
    spend money against arbitrary third-party code."""
    with pytest.raises(typer.BadParameter, match="audit mode does not support L2"):
        audit_repo("gh:foo/bar", rules_only=False)


# --------------------------------------------------------------------------- #
# audit_repo: clone lifecycle (mocked)                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def mock_clone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Mock ``git.Repo.clone_from`` to populate the destination with a tiny
    Python file the scanner can recognise; record the call for assertions.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    calls: list[dict[str, object]] = []

    class _FakeRepo:
        @staticmethod
        def clone_from(url: str, dest: object, **kwargs: object) -> object:
            calls.append({"url": url, "dest": str(dest), "kwargs": kwargs})
            dest_path = Path(str(dest))
            dest_path.mkdir(parents=True, exist_ok=True)
            # Copy fixture into clone target so the subsequent scan finds something.
            for src in fixture.iterdir():
                if src.is_file():
                    (dest_path / src.name).write_bytes(src.read_bytes())
            return None

    import git as _git

    monkeypatch.setattr(_git, "Repo", _FakeRepo)
    return {"calls": calls}


def test_audit_repo_clones_resolved_url(
    mock_clone: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = audit_repo("gh:foo/bar")
    assert rc == 0
    calls = mock_clone["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 1
    assert calls[0]["url"] == "https://github.com/foo/bar.git"


def test_audit_repo_cleans_up_temp_dir(
    mock_clone: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    """Default behaviour: temp clone dir is removed after the report."""
    rc = audit_repo("gh:foo/bar")
    assert rc == 0
    calls = mock_clone["calls"]
    assert isinstance(calls, list)
    dest = Path(str(calls[0]["dest"]))
    assert not dest.exists(), f"audit must clean up temp dir; still present: {dest}"


def test_audit_repo_keeps_clone_when_requested(
    mock_clone: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    rc = audit_repo("gh:foo/bar", keep_clone=True, cache_root=cache)
    assert rc == 0
    # Cache dir should now contain a slugged subdir
    children = list(cache.iterdir())
    assert len(children) == 1
    assert children[0].is_dir()


def test_audit_repo_reuses_existing_clone(
    mock_clone: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache"
    audit_repo("gh:foo/bar", keep_clone=True, cache_root=cache)
    captured_first = capsys.readouterr()

    audit_repo("gh:foo/bar", keep_clone=True, cache_root=cache)
    captured_second = capsys.readouterr()

    assert "cloning" in captured_first.err
    assert "using existing clone" in captured_second.err


def test_audit_repo_returns_nonzero_on_clone_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gitpython raises GitCommandError on bad URLs; audit should surface
    this as a non-zero exit code (not a stack trace)."""
    from git.exc import GitCommandError

    class _FailingRepo:
        @staticmethod
        def clone_from(url: str, dest: object, **kwargs: object) -> object:
            raise GitCommandError(["git", "clone", url], 128, b"repository not found")

    import git as _git

    monkeypatch.setattr(_git, "Repo", _FailingRepo)
    rc = audit_repo("gh:does-not/exist")
    assert rc != 0
