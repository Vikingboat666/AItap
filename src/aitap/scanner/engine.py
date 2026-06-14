"""Top-level scan orchestration.

:func:`scan_project` walks a project tree, dispatches Python files to the
language adapter (:mod:`aitap.scanner.languages.python`) and env/config files
to :mod:`aitap.scanner.rules.env_inspector`, and aggregates the result into a
:class:`ScanResult`.

A small CLI wrapper at the bottom of the module makes
``python -m aitap.scanner.engine <path>`` a usable entry point — it's what the
acceptance criteria pin against. The richer ``aitap scan`` Typer command lives
in :mod:`aitap.scanner.__init__` so the cli-scaffold worktree can wire it
without circular imports.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Iterable
from pathlib import Path

from aitap.scanner.languages.python import scan_python_file
from aitap.scanner.models import (
    Pipeline,
    PromptSite,
    ProviderEvidence,
    ScanResult,
    ScanWarning,
)
from aitap.scanner.rules.env_inspector import is_config_file, is_env_file, scan_paths_for_providers

# Directories we never descend into. Conservative — a user that wants to scan
# inside one (e.g., to audit a vendored fork) can scan that subdir directly.
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".aitap",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "site-packages",
        # Test directories are development artifacts — their contents are
        # almost never real prompts the user wants to iterate on. Users who
        # keep LLM-powered fixtures under tests/ can still scan them with:
        #     aitap scan tests/
        "tests",
        "test",
    }
)


def scan_project(
    project_root: Path | str,
    *,
    ignore_dirs: Iterable[str] | None = None,
    git_commit: str | None = None,
) -> ScanResult:
    """Walk *project_root* and return a populated :class:`ScanResult`.

    Pipeline detection (``ScanResult.pipelines``) is intentionally empty here
    — that's the M2 deliverable owned by ``wt/dataflow``.
    """
    root = Path(project_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"project root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {root}")

    ignored = frozenset(ignore_dirs) if ignore_dirs is not None else DEFAULT_IGNORE_DIRS

    py_files: list[Path] = []
    config_files: list[Path] = []
    for path in _iter_files(root, ignored):
        if path.suffix == ".py":
            py_files.append(path)
        elif is_env_file(path) or is_config_file(path):
            config_files.append(path)

    prompts: list[PromptSite] = []
    warnings: list[ScanWarning] = []
    for py in sorted(py_files):
        sites, file_warnings = scan_python_file(py, root)
        prompts.extend(sites)
        warnings.extend(file_warnings)

    # Post-processing passes that resolve prompts across call boundaries.
    # Run order matters: (1) upgrade UNRESOLVED builder sites by walking
    # their function body and helper calls (so cc-project's
    # ``return [_system(), {"role": "user", "content": user_content}]``
    # surfaces real text); (2) link UNRESOLVED wrapper-call sites to
    # those upgraded builders so ``self._llm.complete(messages, ...)``
    # carries the same text the builder produced. Each pass is pure
    # post-processing — sites it can't touch pass through unchanged.
    from aitap.scanner.rules.cross_call_resolution import (
        link_wrapper_sites_to_builders,
        upgrade_builder_message_lists,
    )

    sorted_py = sorted(py_files)
    prompts = upgrade_builder_message_lists(prompts, sorted_py, root)
    prompts = link_wrapper_sites_to_builders(prompts, sorted_py, root)

    providers: list[ProviderEvidence] = scan_paths_for_providers(sorted(config_files), root)

    # Pipeline detection — dataflow analysis runs on the same Python files
    # we already iterated, but reparses each one so the dataflow module
    # stays decoupled from the prompt-extractor's _PromptSiteVisitor.
    # The cost is acceptable: typical AI projects have <100 files.
    from aitap.scanner.dataflow import detect_pipelines

    pipelines: list[Pipeline] = detect_pipelines(sorted_py, root, prompts)

    return ScanResult(
        project_root=root.as_posix(),
        git_commit=git_commit,
        files_scanned=len(py_files) + len(config_files),
        prompts=prompts,
        pipelines=pipelines,
        providers_detected=providers,
        warnings=warnings,
        l2_used=False,
    )


def _iter_files(root: Path, ignore_dirs: frozenset[str]) -> Iterable[Path]:
    """Yield every regular file under *root*, skipping *ignore_dirs* by name.

    Symlinks to directories are not followed (avoids cycles in venvs that
    mirror site-packages back into the project)."""
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_symlink():
                # Only descend into non-symlinked directories; symlinked files
                # are still readable but symlinked dirs are skipped.
                if entry.is_file():
                    yield entry
                continue
            if entry.is_dir():
                if entry.name in ignore_dirs:
                    continue
                stack.append(entry)
            elif entry.is_file():
                yield entry


# ---------------------------------------------------------------------------
# Module entry point — `python -m aitap.scanner.engine <path>`
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    # On Windows the default stdout encoding is the locale codepage (often GBK
    # on zh-CN), which trips on bullet glyphs rich uses for Markdown lists.
    # Reconfigure to UTF-8 with replacement so the report always renders.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m aitap.scanner.engine",
        description="Scan a project for LLM prompt sites (M1 L1).",
    )
    parser.add_argument("path", type=Path, help="Project root to scan.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit ScanResult as JSON to stdout (default: rich Markdown report).",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=None,
        help="Extra directory names to ignore (repeatable).",
    )
    args = parser.parse_args(argv)

    ignored: list[str] = list(DEFAULT_IGNORE_DIRS)
    if args.ignore:
        ignored.extend(args.ignore)

    try:
        result = scan_project(args.path, ignore_dirs=ignored)
    except (FileNotFoundError, NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        # pydantic v2 supplies model_dump_json with sort and indent options.
        sys.stdout.write(result.model_dump_json(indent=2))
        sys.stdout.write("\n")
        return 0

    # Lazy import keeps `python -m aitap.scanner.engine --json` from paying
    # rich's import cost.
    from aitap.scanner.report import render_terminal_report

    render_terminal_report(result)
    return 0


def main_json(argv: list[str] | None = None) -> str:
    """Programmatic helper — used by tests to grab JSON output without spawning
    a subprocess."""
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    return scan_project(args.path).model_dump_json(indent=2)


def to_json(result: ScanResult, *, indent: int = 2) -> str:
    """Serialize a :class:`ScanResult` to JSON via pydantic."""
    return result.model_dump_json(indent=indent)


__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "main_json",
    "scan_project",
    "to_json",
]


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess in tests
    raise SystemExit(_main())
