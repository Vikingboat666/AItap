"""End-to-end Python-scanner tests for template-definition recognition.

The unit tests in ``test_scanner_template_definitions.py`` pin the rule
behaviour against fabricated AST snippets. These integration tests run the
full :func:`scan_python_file` pipeline against fixture sources shaped like
real production projects (Pet Heaven, LangChain idioms) so we catch
regressions in how the visitor wires the rules in:

- Top-level builder defs surface as :class:`PromptSite`.
- Nested builder defs inside other functions/classes do **not** surface
  (they're helpers to a caller's prompt site, which the SDK-call path
  already records).
- Module-level ``PROMPT_*`` / ``*_TEMPLATE`` constants surface.
- Function-local ``PROMPT_*`` assignments do **not** surface.
- The site shape carries the right tags (``template-definition`` +
  ``builder-function`` | ``module-constant``).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.languages.python import scan_python_file


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """A throwaway project root so the scanner's relative-path resolution
    has somewhere to anchor."""
    return tmp_path


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(dedent(source), encoding="utf-8")
    return file_path


# --------------------------------------------------------------------------- #
# Builder-function integration                                                #
# --------------------------------------------------------------------------- #


def test_top_level_builder_surfaces_as_prompt_site(project_root: Path) -> None:
    """Mirrors Pet Heaven's ``build_personality_messages`` shape."""
    file_path = _write(
        project_root,
        "app/llm/prompt_templates.py",
        '''
        """All prompt templates."""

        def build_personality_messages(pet):
            return [
                {"role": "system", "content": "You are a pet storyteller."},
                {"role": "user", "content": "Describe a pet."},
            ]
        ''',
    )
    sites, warnings = scan_python_file(file_path, project_root)

    assert warnings == []
    assert len(sites) == 1
    site = sites[0]
    assert site.name == "build_personality_messages"
    assert "template-definition" in site.tags
    assert "builder-function" in site.tags
    assert len(site.messages) == 2
    assert site.messages[0].template_text == "You are a pet storyteller."
    assert site.messages[1].template_text == "Describe a pet."


def test_async_builder_surfaces(project_root: Path) -> None:
    file_path = _write(
        project_root,
        "app/agents/templates.py",
        """
        async def build_reflection_messages(memories):
            return [{"role": "system", "content": "Reflect on memories."}]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].name == "build_reflection_messages"


def test_nested_builder_is_not_doubly_reported(project_root: Path) -> None:
    """A ``build_xxx_messages`` defined inside another function is a helper
    to the enclosing call site, not a top-level template definition. The
    SDK-call visitor will pick up the enclosing site; we must not also
    add a template-definition site here.
    """
    file_path = _write(
        project_root,
        "app/agents/runner.py",
        """
        def run_chat(pet):
            def build_internal_messages():
                return [{"role": "user", "content": "hi"}]

            messages = build_internal_messages()
            return messages
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    # No top-level template-definition site; no SDK call site either, so
    # the file should be silent.
    assert sites == []


def test_class_methods_are_not_template_definitions(project_root: Path) -> None:
    """A method on a class named ``build_messages`` is a method on whatever
    class wraps it (probably a runner), not a free template definition.
    """
    file_path = _write(
        project_root,
        "app/agents/runner.py",
        """
        class Runner:
            def build_chat_messages(self):
                return [{"role": "user", "content": "hi"}]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


def test_dynamic_builder_body_still_emits_unresolved_site(project_root: Path) -> None:
    """A ``build_xxx_messages`` whose body we can't parse still emits a
    site so the operator sees the file is worth a look. This matches the
    Pet Heaven shape where ``build_first_meet_story_messages`` composes
    the body with ``dedent(f"...")`` calls that escape our literal parser.
    """
    file_path = _write(
        project_root,
        "app/llm/prompt_templates.py",
        """
        from textwrap import dedent

        def build_first_meet_story_messages(pet, owner):
            return [
                {"role": "system", "content": dedent(f"You are {pet.name}.")},
                {"role": "user", "content": dedent(f"Tell about {owner.name}.")},
            ]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    site = sites[0]
    # The visitor still recorded the definition's existence — confidence
    # downgraded since no literal text was resolvable.
    assert site.name == "build_first_meet_story_messages"
    assert "template-definition" in site.tags


# --------------------------------------------------------------------------- #
# Module-constant integration                                                 #
# --------------------------------------------------------------------------- #


def test_module_level_system_prompt_surfaces(project_root: Path) -> None:
    file_path = _write(
        project_root,
        "app/llm/prompt_templates.py",
        """
        SYSTEM_PROMPT = "You are a helpful storytelling assistant."
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    # _slugify preserves case for valid identifiers — the inventory shows
    # the constant by the same name the source uses.
    assert sites[0].name == "SYSTEM_PROMPT"
    assert "module-constant" in sites[0].tags
    assert sites[0].messages[0].template_text == "You are a helpful storytelling assistant."


def test_triple_quoted_constant_keeps_full_body(project_root: Path) -> None:
    """Mirrors Pet Heaven's ``HEAVEN_WORLD_RULES`` shape."""
    file_path = _write(
        project_root,
        "app/llm/prompt_templates.py",
        '''
        HEAVEN_WORLD_RULES = """
            === RULES ===
            1. Eternal life.
            2. Cross-species harmony.
        """
        ''',
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    text = sites[0].messages[0].template_text
    assert "Eternal life" in text
    assert "Cross-species harmony" in text


def test_function_local_prompt_constant_is_not_surfaced(project_root: Path) -> None:
    """A ``PROMPT_X = ...`` inside a function is a caller-local scratch
    variable, not a real template definition the inventory should claim.
    """
    file_path = _write(
        project_root,
        "app/agents/runner.py",
        """
        def chat():
            SYSTEM_PROMPT = "scratch"
            return SYSTEM_PROMPT
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


def test_unrelated_uppercase_constant_is_ignored(project_root: Path) -> None:
    file_path = _write(
        project_root,
        "app/config.py",
        """
        MAX_RETRIES = 3
        DEFAULT_TIMEOUT = 30
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


# --------------------------------------------------------------------------- #
# Coexistence with SDK-call sites                                             #
# --------------------------------------------------------------------------- #


def test_file_with_both_templates_and_sdk_calls_records_both(
    project_root: Path,
) -> None:
    """A single file can host both a prompt-template definition and a
    direct SDK call — the visitor must surface both.
    """
    file_path = _write(
        project_root,
        "app/agents/runner.py",
        """
        import anthropic

        SYSTEM_PROMPT = "You are a helpful assistant."

        def build_chat_messages():
            return [{"role": "system", "content": "Built-in system."}]

        def run():
            client = anthropic.Anthropic()
            client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
            )
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    # 1 module constant + 1 builder + 1 SDK call = 3 sites
    assert len(sites) == 3
    tag_kinds = {tuple(sorted(s.tags)) for s in sites}
    assert any("module-constant" in t for t in tag_kinds)
    assert any("builder-function" in t for t in tag_kinds)
    # The SDK call site has the anthropic-specific tag instead of
    # the template-definition tag set.
    assert any(
        "template-definition" not in tags and "anthropic" in " ".join(tags).lower()
        for tags in tag_kinds
    )


def test_provider_inferred_from_imports_on_template_definitions(
    project_root: Path,
) -> None:
    """A template-only file with ``import anthropic`` tags its definitions
    with ANTHROPIC even though no call happens here.
    """
    file_path = _write(
        project_root,
        "app/llm/prompt_templates.py",
        """
        import anthropic  # noqa: F401  — only used in the call site, not here

        SYSTEM_PROMPT = "..."
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].provider.value == "anthropic"
