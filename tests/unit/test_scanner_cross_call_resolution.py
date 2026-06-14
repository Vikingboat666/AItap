"""End-to-end tests for ``cross_call_resolution`` — both post-passes.

The module ships two passes:

* :func:`upgrade_builder_message_lists` — turns an UNRESOLVED builder
  site's ``return [_system(), {"role": "user", "content": user_content}]``
  into real text by chasing local Names, module-level constants and
  helper-function returns one hop at a time.
* :func:`link_wrapper_sites_to_builders` — turns an UNRESOLVED
  wrapper-call site (``await self._llm.complete(messages, ...)``)
  into the linked builder's resolved messages, by finding the
  ``messages = build_xxx(...)`` assignment in the same function body.

Both passes are pure post-processing on the ``ScanResult.prompts``
list. The tests fabricate a small project on disk via ``tmp_path`` and
drive :func:`scan_project` end-to-end, then assert tags + message text
+ resolution kinds match what each pass should produce.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aitap.scanner.engine import scan_project
from aitap.scanner.models import PromptSite, TemplateKind

# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


def _by_name(sites: list[PromptSite]) -> dict[str, PromptSite]:
    """Sites have unique names within a small fixture, so a flat dict is
    enough for assertions. (Real projects can have name collisions; the
    scanner disambiguates via the content fingerprint id, which we don't
    rely on here.)"""
    return {site.name: site for site in sites}


# --------------------------------------------------------------------------- #
# Pass 1 — upgrade_builder_message_lists                                       #
# --------------------------------------------------------------------------- #


def test_builder_upgrade_resolves_helper_call_and_name_reference(
    tmp_path: Path,
) -> None:
    """The cc-project shape: ``_system()`` helper + ``user_content`` Name.

    Both messages should land with the ``builder-body-resolved`` tag and
    non-UNRESOLVED kinds. The system message's text traces
    ``content → HEAVEN_WORLD_RULES → dedent(...)`` (two name hops); the
    user message resolves through a single local Name.
    """
    _write(
        tmp_path / "prompts.py",
        '''
        from textwrap import dedent

        HEAVEN_WORLD_RULES = dedent("""
            You are the warm narrator of Pet Heaven.
            Keep tone gentle.
        """).strip()

        def _system(extra: str = "") -> dict[str, str]:
            content = HEAVEN_WORLD_RULES
            return {"role": "system", "content": content}

        def build_digest_messages(pet_name: str) -> list[dict[str, str]]:
            user_content = dedent(f"""
                Write today's diary entry for {pet_name}.
            """)
            return [_system(), {"role": "user", "content": user_content}]
        ''',
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_digest_messages"]
    assert "builder-body-resolved" in builder.tags
    assert len(builder.messages) == 2

    system_msg, user_msg = builder.messages
    assert system_msg.role.value == "system"
    assert system_msg.template_kind is not TemplateKind.UNRESOLVED
    assert "warm narrator of Pet Heaven" in system_msg.template_text

    assert user_msg.role.value == "user"
    assert user_msg.template_kind is TemplateKind.FSTRING
    assert "diary entry" in user_msg.template_text
    assert any(v.name == "pet_name" for v in user_msg.variables)


def test_builder_upgrade_skips_sites_already_resolved(tmp_path: Path) -> None:
    """A builder whose messages are already resolved at L1 must not get
    the ``builder-body-resolved`` tag — the pass only runs on UNRESOLVED
    builders so we don't double-touch what already works.
    """
    _write(
        tmp_path / "prompts.py",
        """
        def build_simple_messages() -> list[dict[str, str]]:
            return [
                {"role": "system", "content": "literal system."},
                {"role": "user", "content": "literal user."},
            ]
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_simple_messages"]
    assert "builder-body-resolved" not in builder.tags
    assert all(m.template_kind is not TemplateKind.UNRESOLVED for m in builder.messages)


def test_builder_upgrade_leaves_truly_dynamic_returns_alone(tmp_path: Path) -> None:
    """When the return isn't a list literal — e.g. ``return messages_var``
    where ``messages_var`` is itself opaque — the pass must not invent
    text. The site stays UNRESOLVED with no upgrade tag.
    """
    _write(
        tmp_path / "prompts.py",
        """
        def build_dynamic_messages(custom: list) -> list[dict[str, str]]:
            messages_var = custom
            return messages_var
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_dynamic_messages"]
    assert "builder-body-resolved" not in builder.tags


def test_builder_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Running the same project twice must produce the same result. The
    second pass sees no UNRESOLVED builders and is a no-op.
    """
    _write(
        tmp_path / "prompts.py",
        """
        from textwrap import dedent

        def _system() -> dict[str, str]:
            return {"role": "system", "content": "be helpful."}

        def build_topic_messages(topic: str) -> list[dict[str, str]]:
            user_content = f"please discuss {topic}."
            return [_system(), {"role": "user", "content": user_content}]
        """,
    )

    first = scan_project(tmp_path)
    second = scan_project(tmp_path)

    a = _by_name(first.prompts)["build_topic_messages"]
    b = _by_name(second.prompts)["build_topic_messages"]
    assert [m.template_text for m in a.messages] == [m.template_text for m in b.messages]
    assert a.tags == b.tags


# --------------------------------------------------------------------------- #
# Pass 2 — link_wrapper_sites_to_builders                                      #
# --------------------------------------------------------------------------- #


def test_wrapper_link_picks_up_builder_in_same_file(tmp_path: Path) -> None:
    """The canonical cc-project shape collapsed into one file: builder
    + wrapper-call inside the same module. The wrapper site gets the
    ``linked-from-builder`` tag and copies the builder's resolved text.
    """
    _write(
        tmp_path / "agent.py",
        """
        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        # builder-function name must match the regex (build_<word>_messages).
        def build_friendly_messages() -> list[dict[str, str]]:
            return [
                {"role": "system", "content": "be friendly."},
                {"role": "user", "content": "hello!"},
            ]

        class Greeter:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def greet(self) -> str:
                messages = build_friendly_messages()
                return await self._llm.complete(messages, task_type="greet")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["greet"]
    assert "wrapper-call" in wrapper.tags
    assert "linked-from-builder" in wrapper.tags
    assert len(wrapper.messages) == 2
    assert "be friendly." in wrapper.messages[0].template_text
    assert "hello!" in wrapper.messages[1].template_text


def test_wrapper_link_uses_messages_kwarg_when_present(tmp_path: Path) -> None:
    """``self._llm.complete(messages=messages, task_type=...)`` should
    link as readily as the bare positional shape.
    """
    _write(
        tmp_path / "agent.py",
        """
        class LLMClient:
            async def complete(self, *, messages, **kwargs):
                ...

        def build_kwarg_messages() -> list[dict[str, str]]:
            return [{"role": "user", "content": "kwarg path."}]

        class Worker:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self) -> None:
                messages = build_kwarg_messages()
                await self._llm.complete(messages=messages, task_type="kw")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["run"]
    assert "linked-from-builder" in wrapper.tags
    assert "kwarg path." in wrapper.messages[0].template_text


def test_wrapper_link_works_across_files(tmp_path: Path) -> None:
    """The cc-project layout splits builders into
    ``backend/app/llm/prompt_templates.py`` and wrappers into
    ``backend/app/agents/*.py``. The link rule must look up the builder
    by name across the project, not just within a single file.
    """
    _write(
        tmp_path / "prompts.py",
        """
        def build_cross_file_messages() -> list[dict[str, str]]:
            return [{"role": "user", "content": "cross-file template."}]
        """,
    )
    _write(
        tmp_path / "agent.py",
        """
        from prompts import build_cross_file_messages

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Cross:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def call(self) -> None:
                messages = build_cross_file_messages()
                await self._llm.complete(messages, task_type="x")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["call"]
    assert "linked-from-builder" in wrapper.tags
    assert "cross-file template." in wrapper.messages[0].template_text


def test_wrapper_link_skips_already_resolved_sites(tmp_path: Path) -> None:
    """A wrapper-call site that already has literal messages must not be
    re-tagged. We never overwrite L1 truth with a link guess.
    """
    _write(
        tmp_path / "agent.py",
        """
        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Inline:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self) -> None:
                await self._llm.complete(
                    [{"role": "user", "content": "inline literal."}],
                    task_type="inline",
                )
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["run"]
    assert "linked-from-builder" not in wrapper.tags
    assert "inline literal." in wrapper.messages[0].template_text


def test_wrapper_link_leaves_unresolvable_alone(tmp_path: Path) -> None:
    """When the RHS isn't a bare-Name call (factory method, ternary,
    arbitrary expression) we shouldn't link — the user would see
    incorrect text otherwise.
    """
    _write(
        tmp_path / "agent.py",
        """
        class Factory:
            def build(self):
                return [{"role": "user", "content": "factory-built."}]

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Worker:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self, factory: Factory) -> None:
                messages = factory.build()
                await self._llm.complete(messages, task_type="x")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["run"]
    assert "linked-from-builder" not in wrapper.tags


# --------------------------------------------------------------------------- #
# Combined — both passes wired together                                        #
# --------------------------------------------------------------------------- #


def test_combined_passes_smoke_resolve_cc_project_shape(tmp_path: Path) -> None:
    """Smoke test for the full pipeline order: upgrade-builder runs
    first so link-wrapper has a resolved builder to copy from.
    """
    _write(
        tmp_path / "prompts.py",
        '''
        from textwrap import dedent

        RULES = dedent("""
            Be concise.
        """).strip()

        def _system() -> dict[str, str]:
            return {"role": "system", "content": RULES}

        def build_combined_messages(name: str) -> list[dict[str, str]]:
            user_content = f"hi {name}."
            return [_system(), {"role": "user", "content": user_content}]
        ''',
    )
    _write(
        tmp_path / "agent.py",
        """
        from prompts import build_combined_messages

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Combined:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self, name: str) -> None:
                messages = build_combined_messages(name)
                await self._llm.complete(messages, task_type="combo")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_combined_messages"]
    assert "builder-body-resolved" in builder.tags
    assert "Be concise." in builder.messages[0].template_text
    assert "hi {name}." in builder.messages[1].template_text

    wrapper = sites["run"]
    assert "linked-from-builder" in wrapper.tags
    assert "Be concise." in wrapper.messages[0].template_text
    assert "hi {name}." in wrapper.messages[1].template_text


@pytest.mark.parametrize(
    "messages_call",
    [
        "self._llm.complete(messages, task_type='x')",
        "self._llm.complete(messages=messages, task_type='x')",
        "await self._llm.complete(messages, task_type='x')",
    ],
)
def test_wrapper_link_handles_async_and_kwarg_variants(tmp_path: Path, messages_call: str) -> None:
    """The three call shapes that show up in cc-project's agents must
    all link successfully. The shared assertion: ``linked-from-builder``
    tag plus the builder's resolved content on the wrapper site.
    """
    _write(
        tmp_path / "agent.py",
        f"""
        class LLMClient:
            async def complete(self, *args, **kwargs):
                ...

        def build_variant_messages() -> list[dict[str, str]]:
            return [{{"role": "user", "content": "variant text."}}]

        class Worker:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self) -> None:
                messages = build_variant_messages()
                {messages_call}
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["run"]
    assert "linked-from-builder" in wrapper.tags
    assert "variant text." in wrapper.messages[0].template_text


# --------------------------------------------------------------------------- #
# Boundary cases — pin the conservative behaviour documented in the           #
# module docstring's "What this does NOT catch" section so future refactors   #
# know which corners we deliberately left alone.                              #
# --------------------------------------------------------------------------- #


def test_same_named_builder_across_files_picks_one_silently(tmp_path: Path) -> None:
    """Two files each defining ``def build_dup_messages()`` collide on
    the flat ``site.name`` index in pass 2. Documented behaviour: the
    dict comprehension keeps the **source-order last** seen entry;
    wrapper sites in either file get whichever entry won.

    This test pins the behaviour so a future change that adds
    import-resolution (the documented follow-up) trips it intentionally
    — and then gets a corrected expectation in the same PR.
    """
    _write(
        tmp_path / "a_prompts.py",
        """
        def build_dup_messages() -> list[dict[str, str]]:
            return [{"role": "user", "content": "from file A."}]
        """,
    )
    _write(
        tmp_path / "b_prompts.py",
        """
        def build_dup_messages() -> list[dict[str, str]]:
            return [{"role": "user", "content": "from file B."}]
        """,
    )
    _write(
        tmp_path / "agent.py",
        """
        from a_prompts import build_dup_messages

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Caller:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self) -> None:
                messages = build_dup_messages()
                await self._llm.complete(messages, task_type="x")
        """,
    )

    result = scan_project(tmp_path)

    # Two builder sites exist (one per file). Both are resolved (pass 2
    # filters to ``not _all_unresolved``); the wrapper picks up one of
    # the two texts, currently the source-order last (b_prompts.py).
    # The exact "which one wins" is unstable across future refactors —
    # asserting only that the wrapper got SOMETHING resolved + the tag.
    # (Cannot use ``_by_name`` here: the two builder sites share a name.)
    wrapper = next(s for s in result.prompts if s.name == "run")
    assert "linked-from-builder" in wrapper.tags
    text = wrapper.messages[0].template_text
    assert text in {"from file A.", "from file B."}


def test_builder_with_return_inside_try_block_stays_unresolved(
    tmp_path: Path,
) -> None:
    """A builder whose ``return [...]`` sits inside a ``try`` block
    is intentionally **not** upgraded by pass 1 — :func:`_find_first_return`
    only walks the top-level body to avoid claiming a return whose
    branch we don't know runs.

    Pin the conservative behaviour so a future "single-return-in-try"
    extension trips this test intentionally.
    """
    _write(
        tmp_path / "prompts.py",
        """
        def build_try_block_messages() -> list[dict[str, str]]:
            try:
                return [{"role": "user", "content": "inside try."}]
            except Exception:
                return [{"role": "user", "content": "fallback."}]
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_try_block_messages"]
    # The site exists (template_definitions still tagged it as a builder),
    # but pass 1 left it alone — no ``builder-body-resolved`` tag.
    assert "builder-function" in builder.tags
    assert "builder-body-resolved" not in builder.tags


def test_wrapper_link_with_sibling_call_in_kwarg_still_links(
    tmp_path: Path,
) -> None:
    """When the wrapper-call shares a line with another ``ast.Call``
    (here a helper call inside the ``task_type=`` kwarg),
    :func:`_extract_first_positional_or_kw_name` must still pick the
    wrapper's own first positional rather than the sibling Call's args.

    Pins that the per-Call iteration in
    :func:`_extract_first_positional_or_kw_name` keeps walking past Call
    nodes whose ``args[0]`` isn't a Name, so the canonical wrapper's
    Name ``messages`` argument still wins.
    """
    _write(
        tmp_path / "agent.py",
        """
        def build_sibling_messages() -> list[dict[str, str]]:
            return [{"role": "user", "content": "sibling-call template."}]

        def label_for(category: str) -> str:
            return category.upper()

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Sibling:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def run(self) -> None:
                messages = build_sibling_messages()
                await self._llm.complete(messages, task_type=label_for("digest"))
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    wrapper = sites["run"]
    # The sibling ``label_for("digest")`` Call lives on the same line
    # as the wrapper; pass 2 still picks ``messages`` and links.
    assert "linked-from-builder" in wrapper.tags
    assert "sibling-call template." in wrapper.messages[0].template_text


# --------------------------------------------------------------------------- #
# Partial-builder upgrade (PR landing this fix)                               #
# --------------------------------------------------------------------------- #
#
# Before this fix, ``_is_builder_upgrade_candidate`` required *every*
# message to be UNRESOLVED before pass 1 would run. cc-project's
# ``build_importance_scoring_messages`` shape (literal system + Name-
# reference user) landed as 1/2 resolved at L1 and stayed there
# forever because pass 1 skipped the site. The new behaviour: any
# UNRESOLVED message triggers eligibility; the upgrader iterates
# pairwise with the AST elements and upgrades only what L1 missed,
# leaving the literal slot byte-for-byte unchanged.


def test_builder_upgrade_fills_partial_resolution_keeps_l1_literal(
    tmp_path: Path,
) -> None:
    """The exact cc-project ``build_importance_scoring_messages``
    shape: one literal system message + one user message whose
    content is a Name reference to a function-local
    ``user_content = dedent(f"...").strip()`` assignment.

    Before this fix the site stayed at 1/2 (system literal resolved,
    user UNRESOLVED). After: 2/2, system message preserved
    byte-for-byte, user message resolved via the post-pass.
    """
    _write(
        tmp_path / "prompts.py",
        """
        from textwrap import dedent

        SYSTEM_LINE = "You are a concise scoring assistant. Return JSON."

        def build_importance_scoring_messages(memory: str) -> list[dict[str, str]]:
            user_content = dedent(f'''
                Rate the importance of: {memory}
                Return only: {{"importance": <float>}}
            ''').strip()
            return [
                {"role": "system", "content": SYSTEM_LINE},
                {"role": "user", "content": user_content},
            ]
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_importance_scoring_messages"]
    assert "builder-body-resolved" in builder.tags
    assert len(builder.messages) == 2

    system_msg, user_msg = builder.messages
    # System: byte-for-byte from L1 (kind=LITERAL), no rewrite.
    assert system_msg.role.value == "system"
    assert system_msg.template_kind is TemplateKind.LITERAL
    assert system_msg.template_text == ("You are a concise scoring assistant. Return JSON.")
    # User: post-pass filled in the dedent-stripped f-string.
    assert user_msg.role.value == "user"
    assert user_msg.template_kind is TemplateKind.FSTRING
    assert "Rate the importance of:" in user_msg.template_text
    assert any(v.name == "memory" for v in user_msg.variables)


def test_builder_upgrade_keeps_l1_resolved_message_when_post_pass_cant_resolve_other(
    tmp_path: Path,
) -> None:
    """A builder with one L1-resolved literal + one truly
    unresolvable Name (no matching assignment in the function body
    or module) → post-pass keeps the literal byte-for-byte and
    leaves the unresolvable slot UNRESOLVED. The site is **not**
    tagged ``builder-body-resolved`` because nothing flipped.
    """
    _write(
        tmp_path / "prompts.py",
        """
        def build_partial_messages(payload: dict) -> list[dict[str, str]]:
            return [
                {"role": "system", "content": "literal system."},
                {"role": "user", "content": payload["from_runtime"]},
            ]
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_partial_messages"]
    # Nothing flipped → the post-pass returned None → no tag added.
    assert "builder-body-resolved" not in builder.tags
    # The L1 literal is still readable.
    system_msg = builder.messages[0]
    assert "literal system." in system_msg.template_text


def test_builder_upgrade_partial_run_is_idempotent(tmp_path: Path) -> None:
    """A partial-upgrade run leaves the site in a state where every
    message is either L1-resolved or post-pass-resolved. A second
    run finds ``any(unresolved)`` False on the upgraded site and
    treats it like a fully-resolved builder — no second tag, no
    rewrite. Pin the no-op so a regression that double-runs the
    upgrade (or strips a tag) trips here.
    """
    _write(
        tmp_path / "prompts.py",
        """
        from textwrap import dedent

        def build_topic_partial_messages(topic: str) -> list[dict[str, str]]:
            user_content = dedent(f'''
                please discuss {topic}.
            ''').strip()
            return [
                {"role": "system", "content": "literal system."},
                {"role": "user", "content": user_content},
            ]
        """,
    )

    first = scan_project(tmp_path)
    second = scan_project(tmp_path)

    a = _by_name(first.prompts)["build_topic_partial_messages"]
    b = _by_name(second.prompts)["build_topic_partial_messages"]
    assert [m.template_text for m in a.messages] == [m.template_text for m in b.messages]
    assert a.tags == b.tags
    # The tag must appear exactly once on each pass, not accumulate
    # across runs.
    assert a.tags.count("builder-body-resolved") == 1


def test_partial_builder_unblocks_wrapper_link_to_full_text(tmp_path: Path) -> None:
    """End-to-end: when a partial builder gets upgraded to 2/2 by
    pass 1, a wrapper-call site that linked at 1/2 before now picks
    up the full text via pass 2. The cc-project ``score_importance``
    wrapper was the live case — fixing the partial builder
    cascades."""
    _write(
        tmp_path / "agent.py",
        """
        from textwrap import dedent

        def build_partial_link_messages(memory: str) -> list[dict[str, str]]:
            user_content = dedent(f'''
                Memory to score: {memory}.
            ''').strip()
            return [
                {"role": "system", "content": "judge memory importance."},
                {"role": "user", "content": user_content},
            ]

        class LLMClient:
            async def complete(self, messages, **kwargs):
                ...

        class Scorer:
            def __init__(self) -> None:
                self._llm = LLMClient()

            async def score(self, memory: str) -> None:
                messages = build_partial_link_messages(memory)
                await self._llm.complete(messages, task_type="score")
        """,
    )

    result = scan_project(tmp_path)
    sites = _by_name(result.prompts)

    builder = sites["build_partial_link_messages"]
    assert "builder-body-resolved" in builder.tags
    assert all(m.template_kind is not TemplateKind.UNRESOLVED for m in builder.messages)

    wrapper = sites["score"]
    assert "linked-from-builder" in wrapper.tags
    # Both messages flow through — system literal AND post-pass-
    # resolved user — to the wrapper site.
    assert "judge memory importance." in wrapper.messages[0].template_text
    assert "Memory to score:" in wrapper.messages[1].template_text
