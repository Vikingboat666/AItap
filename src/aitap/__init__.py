"""aitap — Zero-config CLI to discover, test, and iterate prompts in your AI codebase."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth is ``pyproject.toml``; read it back from the
    # installed package metadata so ``aitap --version`` can never drift.
    __version__ = version("aitap")
except PackageNotFoundError:  # pragma: no cover - running from a source tree, not installed
    __version__ = "0.0.0.dev0"
