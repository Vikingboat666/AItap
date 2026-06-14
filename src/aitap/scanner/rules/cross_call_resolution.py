"""Post-processing passes that resolve prompts across call boundaries.

The cc-project (Pet Heaven) eval surfaced the long tail after PRs #46 +
#47 landed: every agent file holds a wrapper-call site like ::

    async def generate(self, pet, ...):
        messages = build_digest_messages(pet)        # already a builder
        raw = await self._llm.complete(              # wrapper-call site
            messages,                                #   first positional = the same Name
            task_type="digest",
        )

PR #46 catches ``build_digest_messages`` as a template-definition; PR #47
catches the ``self._llm.complete(messages, ...)`` site as a wrapper-call.
But two gaps left every agent prompt empty in the UI:

1. **Wrapper-call → builder linkage**. The wrapper's ``messages`` is a
   Name reference, so the per-site AST extractor can't see the literal
   text the builder produced. :func:`link_wrapper_sites_to_builders`
   closes this by walking the enclosing function for the
   ``messages = build_xxx(...)`` assignment and copying the builder's
   resolved messages onto the wrapper.

2. **Builder body resolution**. Most cc-project builders look like ::

       def build_digest_messages(...):
           activities_block = "..."
           user_content = dedent(f\"\"\"...\"\"\")
           return [_system(), {"role": "user", "content": user_content}]

   The L1 extractor sees ``_system()`` as an opaque Call and
   ``user_content`` as an opaque Name, so the builder site itself ends
   up UNRESOLVED — and pass (1) above has nothing to link to.
   :func:`upgrade_builder_message_lists` walks each UNRESOLVED builder
   and resolves names by looking back through the function body (and
   module-level constants like ``HEAVEN_WORLD_RULES``) and helper calls
   by recursing into the helper's return statement.

Both passes are pure post-processing. They reparse the Python files
they need (cached per call) and never mutate the existing per-site
visitor. Sites they can't upgrade pass through unchanged so downstream
tools see no reshuffling.

What this catches
-----------------

- Builder body: ``return [_system(), {"role": "user", "content": user_content}]``
  with ``_system`` defined in the same module and ``user_content``
  assigned within the same function from a literal / f-string / dedent.
- Builder body: ``return [{"role": "system", "content": HEAVEN_WORLD_RULES}, ...]``
  with module-level constant ``HEAVEN_WORLD_RULES = dedent(\"\"\"...\"\"\")``.
- Wrapper link: ``messages = build_foo_messages(...)`` followed by
  ``self.x.method(messages, ...)`` or
  ``self.x.method(messages=messages, ...)`` in the same function body.
- Async function bodies — agents are async-heavy and the assignment
  shape doesn't change between sync and async.
- Cross-file linkage: the builder can live in a different file (the
  cc-project shape: agents call ``build_<task>_messages`` defined in
  ``app/llm/prompt_templates.py``).

What this does **not** catch
----------------------------

- ``messages = factory().build(...)`` — RHS is a method chain.
- ``messages = build_a() if cond else build_b()`` — ternary with two
  branches; we'd have to pick one and risk lying.
- Wrapper-call sites whose messages parameter is dropped through the
  SDK wrapper file itself (e.g. ``LLMClient._call_ollama(messages)``).
  Tracking that chain crosses a method-dispatch boundary and is
  follow-up territory.
- Helper functions whose bodies themselves use control flow (a
  ``_system()`` that returns different dicts based on a flag). We pick
  the first ``return`` we see and stop.
- Branch-local rebinds of an already-bound Name inside ``if`` / ``else``
  / ``try`` blocks. ``_collect_local_assignments`` only walks the
  function's top-level body, so for ::

      content = HEAVEN_WORLD_RULES
      if extra:
          content = content + "\\n\\n" + extra  # NOT collected

  the rebind under ``if`` does not participate in last-wins. The base
  value is what surfaces. This is deliberate: branch-conditional text
  isn't safe to claim as the canonical resolution.
- Builder ``return`` statements nested inside ``try`` / ``except`` /
  ``if`` blocks. :func:`_find_first_return` only walks the function's
  top-level body to avoid picking a branch we don't know runs. Builders
  shaped like ``try: return [...] except: return None`` therefore stay
  UNRESOLVED — this is one root cause of why cc-project lands at 8/9
  rather than 9/9 fully-resolved builders.
- Helpers whose return value is itself a list of messages or contains a
  ``*starred`` spread (``return [system_msg, *build_extra(), user_msg]``).
  :func:`_resolve_list_item_to_message` only recurses one level into a
  helper *and* only when that helper returns a single ``ast.Dict``. The
  spread / list-returning shape degrades to a single UNRESOLVED
  placeholder, so the message count may not match the runtime list
  length.
- Same-named builder functions defined in two different files. Pass 2
  indexes builders by ``site.name`` into a flat dict, so on collision
  the source-order **last** seen wins. Pass 2 does not consult the
  wrapper's import table to disambiguate — adding that lookup is a
  follow-up.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from aitap.scanner.models import Message, PromptSite, Role, TemplateKind, TemplateVariable
from aitap.scanner.rules.prompt_extractor import extract_template

LINKED_TAG = "linked-from-builder"
UPGRADED_TAG = "builder-body-resolved"


# ---------------------------------------------------------------------------
# Pass 1: upgrade UNRESOLVED builder sites by resolving names + helper calls
# ---------------------------------------------------------------------------


def upgrade_builder_message_lists(
    sites: list[PromptSite],
    files: Iterable[Path],
    project_root: Path,
) -> list[PromptSite]:
    """Return *sites* with eligible builder-function sites' UNRESOLVED
    messages replaced by ones the in-function / module-level resolver
    could compute.

    Sites the rule doesn't touch are returned in their original order
    so downstream consumers see no reshuffling.

    Idempotent: a second run finds no UNRESOLVED builders and is a
    no-op.
    """
    ast_cache: dict[str, ast.Module] = {}
    file_index: dict[str, Path] = {}
    for file_path in files:
        rel = _relative(file_path, project_root)
        file_index[rel] = file_path

    updated: list[PromptSite] = []
    for site in sites:
        if not _is_builder_upgrade_candidate(site):
            updated.append(site)
            continue

        file_path = file_index.get(site.location.file)
        if file_path is None:
            updated.append(site)
            continue

        tree = _get_tree(ast_cache, file_path)
        if tree is None:
            updated.append(site)
            continue

        func = _find_function_def_by_name(tree, site.name)
        if func is None:
            updated.append(site)
            continue

        upgraded = _upgrade_builder_message_list(func, tree, site.messages)
        if upgraded is None:
            updated.append(site)
            continue

        new_tags = list(site.tags)
        if UPGRADED_TAG not in new_tags:
            new_tags.append(UPGRADED_TAG)
        updated.append(site.model_copy(update={"messages": upgraded, "tags": new_tags}))
    return updated


# ---------------------------------------------------------------------------
# Pass 2: link wrapper-call sites to their builder source
# ---------------------------------------------------------------------------


def link_wrapper_sites_to_builders(
    sites: list[PromptSite],
    files: Iterable[Path],
    project_root: Path,
) -> list[PromptSite]:
    """Return *sites* with eligible wrapper-call sites' messages upgraded
    from UNRESOLVED to the linked builder's resolved messages.

    Sites the rule doesn't touch are returned in their original order
    so downstream consumers (store, dataflow, UI) see no reshuffling.

    The pass is idempotent — running it twice produces the same list,
    because the second run's wrapper-call messages are no longer
    UNRESOLVED and skip the lookup branch.
    """
    # Index template-definition builder functions by their site name.
    # We trust the slugify already done by the visitor (it preserves the
    # original function identifier for builder sites, which is what
    # downstream call expressions reference).
    # Same-named builder in two different files? The dict comprehension
    # collapses to the source-order last seen (Python dict semantics).
    # cc-project doesn't collide so this hasn't bitten yet, but the
    # behaviour is documented in the module docstring's "What this does
    # not catch" section. Adding import-resolution to disambiguate is a
    # follow-up.
    builder_messages_by_name: dict[str, list[Message]] = {
        site.name: list(site.messages)
        for site in sites
        if "builder-function" in site.tags and not _all_unresolved(site.messages)
    }
    if not builder_messages_by_name:
        return sites

    # Cache parsed ASTs per file so we don't reparse for every site in
    # the same file. cc-project's ``test_llm_client.py`` alone holds 7
    # wrapper-call sites; without the cache that's 7 parse rounds for
    # one file.
    ast_cache: dict[str, ast.Module] = {}
    file_index: dict[str, Path] = {}
    for file_path in files:
        rel = _relative(file_path, project_root)
        file_index[rel] = file_path

    updated: list[PromptSite] = []
    for site in sites:
        if not _is_link_candidate(site):
            updated.append(site)
            continue

        file_path = file_index.get(site.location.file)
        if file_path is None:
            updated.append(site)
            continue

        tree = _get_tree(ast_cache, file_path)
        if tree is None:
            updated.append(site)
            continue

        linked = _try_link(site, tree, builder_messages_by_name)
        updated.append(linked if linked is not None else site)
    return updated


# ---------------------------------------------------------------------------
# Pass 1 helpers: builder body upgrade
# ---------------------------------------------------------------------------


def _is_builder_upgrade_candidate(site: PromptSite) -> bool:
    """Attempt upgrade on any builder-function site with at least one
    UNRESOLVED message.

    Previously the gate was ``_all_unresolved`` — pass 1 only ran when
    L1 hit a complete miss. That left partial builders untouched, even
    though L1 had already resolved the literal half. The cc-project
    `build_importance_scoring_messages` case surfaced this: the system
    message was a literal string (resolves at L1), but the user
    message's ``content=user_content`` Name reference fell through to
    UNRESOLVED. The whole site stayed at 1/2 because pass 1's
    all-or-nothing gate skipped it.

    The new gate runs whenever **any** message is UNRESOLVED. The
    upgrader iterates pairwise with the return-list AST items, keeps
    L1-resolved messages byte-for-byte, and only attempts to resolve
    the unresolved positions. Pure addition: a builder that L1
    already fully resolved still hits the early ``not any unresolved``
    check and gets skipped.
    """
    if "builder-function" not in site.tags:
        return False
    return any(m.template_kind is TemplateKind.UNRESOLVED for m in site.messages)


def _find_function_def_by_name(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Locate the top-level ``def name(...)`` (sync or async).

    Builder sites preserve the original function identifier as
    ``site.name``, so a direct lookup works without fuzzy matching.
    Nested definitions are skipped — a builder defined inside another
    function isn't a discoverable template at L1.
    """
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _upgrade_builder_message_list(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    module: ast.Module,
    existing: list[Message],
) -> list[Message] | None:
    """Try to upgrade UNRESOLVED slots in *existing* by walking *func*'s
    return statement with local-var + helper-call + module-constant
    resolution.

    Returns a new message list with L1-resolved entries preserved
    byte-for-byte and post-pass-resolved entries swapped in, or
    ``None`` when the pass didn't actually flip any UNRESOLVED entry
    to a resolved one (caller treats ``None`` as "no change, keep the
    site as-is").

    The pairwise iteration over *existing* and ``return_node.value.elts``
    assumes the L1 extractor produces one :class:`Message` per source
    list element — which it does (see
    :func:`aitap.scanner.rules.prompt_extractor.extract_messages`). If
    the lengths disagree (a future scanner change that collapses or
    expands messages), we bail to ``None`` rather than risk an
    off-by-one slot misalignment between the L1 read and the
    post-pass write.
    """
    return_node = _find_first_return(func)
    if return_node is None or not isinstance(return_node.value, ast.List):
        return None

    elts = return_node.value.elts
    locals_map = _collect_local_assignments(func)
    module_consts = _collect_module_assignments(module)
    module_funcs = _collect_module_functions(module)

    # Two-mode handling of the length-mismatch case.
    #
    # When *every* L1 message is UNRESOLVED, L1's ``_messages_from_function_body``
    # sometimes collapses the whole list down to a single UNRESOLVED
    # fallback (it bails to the "emit one placeholder so the
    # definition shows up" path). In that case there is nothing to
    # preserve from L1, and we can safely rebuild a fresh list aligned
    # with the AST ``return [...]`` elements — that is what pass 1
    # has always done.
    #
    # When *some* L1 messages are resolved, length alignment matters:
    # we need to know which slot to keep and which to upgrade. A
    # length mismatch in that case means the L1 reader saw something
    # we can't safely realign, so we bail to ``None`` rather than
    # risk swapping a resolved slot with a freshly resolved one in
    # the wrong position.
    all_unresolved = all(m.template_kind is TemplateKind.UNRESOLVED for m in existing)
    if not all_unresolved and len(elts) != len(existing):
        return None

    if all_unresolved:
        # Rebuild from AST — the historical behaviour. L1 had nothing
        # worth preserving, so the post-pass owns the whole list.
        out: list[Message] = []
        any_resolved = False
        for item in elts:
            msg = _resolve_list_item_to_message(item, locals_map, module_consts, module_funcs)
            if msg is None:
                out.append(
                    Message(
                        role=Role.USER,
                        template_text="",
                        template_kind=TemplateKind.UNRESOLVED,
                    )
                )
                continue
            if msg.template_kind is not TemplateKind.UNRESOLVED:
                any_resolved = True
            out.append(msg)
        return out if any_resolved else None

    # Mixed case: lengths match, some L1 messages are resolved. Keep
    # L1's resolved entries byte-for-byte, only try to upgrade the
    # UNRESOLVED ones.
    out_mixed: list[Message] = []
    flipped_to_resolved = False
    for item, current in zip(elts, existing, strict=True):
        if current.template_kind is not TemplateKind.UNRESOLVED:
            out_mixed.append(current)
            continue
        upgraded = _resolve_list_item_to_message(item, locals_map, module_consts, module_funcs)
        if upgraded is None or upgraded.template_kind is TemplateKind.UNRESOLVED:
            out_mixed.append(current)
            continue
        out_mixed.append(upgraded)
        flipped_to_resolved = True
    return out_mixed if flipped_to_resolved else None


def _resolve_list_item_to_message(
    item: ast.expr,
    locals_map: dict[str, ast.expr],
    module_consts: dict[str, ast.expr],
    module_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> Message | None:
    """Resolve one item of a builder's return list to a :class:`Message`.

    Recognises:

    * ``{"role": ..., "content": ...}`` dict — content may be a literal,
      f-string, Name (looked up in locals → module-level constants), or
      ``dedent(...)`` etc. (delegated to :func:`extract_template`).
    * ``_helper()`` call — the helper must be a module-level function;
      we recurse into its return statement (one level only — no chains
      of helpers calling helpers).
    """
    if isinstance(item, ast.Dict):
        return _resolve_dict_to_message(item, locals_map, module_consts)

    if isinstance(item, ast.Call) and isinstance(item.func, ast.Name):
        helper = module_funcs.get(item.func.id)
        if helper is None:
            return None
        helper_return = _find_first_return(helper)
        if helper_return is None:
            return None
        # Helper returns a single dict (canonical for _system()).
        if isinstance(helper_return.value, ast.Dict):
            helper_locals = _collect_local_assignments(helper)
            return _resolve_dict_to_message(helper_return.value, helper_locals, module_consts)
    return None


def _resolve_dict_to_message(
    node: ast.Dict,
    locals_map: dict[str, ast.expr],
    module_consts: dict[str, ast.expr],
) -> Message | None:
    """Parse a ``{"role": ..., "content": ...}`` dict literal, resolving
    a Name in the ``content`` slot via *locals_map* → *module_consts*.
    """
    role = Role.USER
    role_seen = False
    content_node: ast.expr | None = None
    for key, value in zip(node.keys, node.values, strict=False):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if key.value == "role":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                try:
                    role = Role(value.value)
                except ValueError:
                    role = Role.USER
                role_seen = True
        elif key.value == "content":
            content_node = value

    if content_node is None or not role_seen:
        return None

    resolved = _resolve_expr(content_node, locals_map, module_consts)
    if resolved is None:
        return None
    text, kind, variables = resolved
    return Message(role=role, template_text=text, template_kind=kind, variables=variables)


_NAME_HOP_LIMIT = 4
"""Maximum number of ``Name → Name`` aliasing hops :func:`_resolve_expr`
will chase before giving up. cc-project's chain is exactly two
(``content`` → ``HEAVEN_WORLD_RULES`` → ``dedent(...).strip()``);
four leaves headroom for slightly longer aliasing chains without
risking runaway loops if a helper aliases itself."""


def _resolve_expr(
    node: ast.expr,
    locals_map: dict[str, ast.expr],
    module_consts: dict[str, ast.expr],
) -> tuple[str, TemplateKind, list[TemplateVariable]] | None:
    """Run :func:`extract_template` on *node*, chasing Name references
    through *locals_map* then *module_consts* up to :data:`_NAME_HOP_LIMIT`
    hops. This covers cc-project's ``content = HEAVEN_WORLD_RULES``
    chain inside ``_system()`` (one local hop) plus the
    ``HEAVEN_WORLD_RULES = dedent(...).strip()`` module hop the next
    layer down.

    The hop limit protects against pathological aliasing
    (``a = b; b = a``) and keeps the cost linear in the chain depth.
    """
    target: ast.AST = node
    seen: set[str] = set()
    for _ in range(_NAME_HOP_LIMIT):
        if not isinstance(target, ast.Name):
            break
        if target.id in seen:
            return None
        seen.add(target.id)
        replacement = locals_map.get(target.id) or module_consts.get(target.id)
        if replacement is None:
            return None
        target = replacement
    text, kind, variables = extract_template(target)
    if kind is TemplateKind.UNRESOLVED:
        return None
    return text, kind, list(variables)


def _find_first_return(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Return | None:
    """Return the first ``return <expr>`` in *func*'s top-level body.

    Most builders are linear (one return at the end). We walk only the
    top level to avoid grabbing a return tucked inside a try/except
    branch — that's the shape where we'd risk picking the wrong path.

    Caveat: a *legitimate* builder whose only return sits inside
    ``try`` / ``if`` / ``except`` will be skipped by this pass and stay
    UNRESOLVED. cc-project's eval shows 8/9 builders fully resolved —
    the 1/9 gap is partly this conservatism. A follow-up could add a
    "single-return-in-try" path that's still safe to claim.
    """
    for stmt in func.body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            return stmt
    return None


def _collect_local_assignments(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.expr]:
    """Map ``Name`` → most-recent top-level RHS for ``<name> = <expr>``
    (and ``<name>: T = <expr>``) statements in *func*'s body.

    We deliberately walk only the *top level* of the function body —
    branch-local rebinds (``if extra: content = content + ...``,
    ``try: x = ... except: x = ...``) are not collected. That keeps us
    from inventing text from a code path that may not run; the base
    binding at the top level is what we treat as the canonical value.

    When the same name has multiple top-level assignments, source-order
    last wins (matches Python's actual binding semantics — the last
    top-level assignment is what's live by the time ``return`` runs).
    """
    locals_map: dict[str, ast.expr] = {}
    for stmt in func.body:
        targets, value = _unpack_assign(stmt)
        if targets is None or value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                locals_map[target.id] = value
    return locals_map


def _collect_module_assignments(module: ast.Module) -> dict[str, ast.expr]:
    """Top-level ``<NAME> = <expr>`` constants. cc-project's
    ``HEAVEN_WORLD_RULES`` lives at this scope."""
    consts: dict[str, ast.expr] = {}
    for stmt in module.body:
        targets, value = _unpack_assign(stmt)
        if targets is None or value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                consts[target.id] = value
    return consts


def _collect_module_functions(
    module: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Top-level helper functions like ``_system()`` that builders call."""
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for stmt in module.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[stmt.name] = stmt
    return funcs


# ---------------------------------------------------------------------------
# Pass 2 helpers: wrapper-call → builder link
# ---------------------------------------------------------------------------


def _is_link_candidate(site: PromptSite) -> bool:
    """A wrapper-call site whose messages are entirely UNRESOLVED is the
    only shape this pass tries to upgrade.

    Sites that already carry text (``messages=[{"role": ...}, ...]`` was
    literal at the call) are left alone — the pass never overrides what
    L1 already resolved correctly.
    """
    if "wrapper-call" not in site.tags:
        return False
    return _all_unresolved(site.messages)


def _all_unresolved(messages: list[Message]) -> bool:
    return all(m.template_kind is TemplateKind.UNRESOLVED for m in messages)


def _try_link(
    site: PromptSite,
    tree: ast.Module,
    builder_messages_by_name: dict[str, list[Message]],
) -> PromptSite | None:
    """Walk *tree* looking for the function enclosing *site* and, within
    its body, a ``<name> = <builder>()`` assignment that matches the
    name flowing into the wrapper-call. Returns an upgraded
    :class:`PromptSite` or ``None`` if nothing matched.
    """
    enclosing = _find_enclosing_function(tree, site.location.line_start)
    if enclosing is None:
        return None

    var_name = _extract_first_positional_or_kw_name(enclosing, site.location.line_start)
    if var_name is None:
        return None

    builder_name = _find_builder_call_for_assignment(enclosing, var_name)
    if builder_name is None:
        return None

    messages = builder_messages_by_name.get(builder_name)
    if messages is None:
        return None

    new_tags = list(site.tags)
    if LINKED_TAG not in new_tags:
        new_tags.append(LINKED_TAG)
    return site.model_copy(update={"messages": messages, "tags": new_tags})


def _find_enclosing_function(
    tree: ast.Module, line: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the innermost function definition whose body covers *line*.

    Uses ``lineno`` and ``end_lineno`` (Python ≥ 3.8 always sets both).
    The visitor records ``line_start`` of the *call*, which is always
    inside the function's body range.
    """
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None:
            continue
        if not (start <= line <= end):
            continue
        # Prefer the innermost (smallest range) function. The simplest
        # ordering: smaller end - start wins on tie. ast.walk doesn't
        # promise any ordering on which node we see first, so we have
        # to track best.
        if best is None or (end - start) < (best.end_lineno - best.lineno):  # type: ignore[operator]
            best = node
    return best


def _extract_first_positional_or_kw_name(
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef, line: int
) -> str | None:
    """Find the wrapper-call expression at *line* inside *enclosing*'s
    body and return the variable name flowing into its ``messages``
    parameter, or the first positional if no ``messages=`` kwarg is
    present.

    Returns ``None`` when the parameter isn't a bare Name (literal lists,
    method-chain calls, complex expressions all fall through).
    """
    for node in ast.walk(enclosing):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node, "lineno", None) != line:
            continue
        # 1) ``messages=<name>`` kwarg wins when present.
        for kw in node.keywords:
            if kw.arg == "messages" and isinstance(kw.value, ast.Name):
                return kw.value.id
        # 2) Otherwise first positional, when it's a Name.
        if node.args and isinstance(node.args[0], ast.Name):
            return node.args[0].id
    return None


def _find_builder_call_for_assignment(
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef, target_name: str
) -> str | None:
    """Find ``<target_name> = <builder_name>(...)`` inside *enclosing*'s
    body and return the builder's callable name, or ``None`` when the
    RHS isn't a bare-Name call.

    Walks the entire function body — we deliberately ignore branching
    (the rule's scope notes flag the risk). If the same name is assigned
    twice with different RHS calls, we return the **last** one (source-
    order) on the assumption that's what flows into the call site below.
    """
    found: str | None = None
    for node in ast.walk(enclosing):
        targets, value = _unpack_assign(node)
        if targets is None or value is None:
            continue
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == target_name):
                continue
            builder = _builder_name_from_call(value)
            if builder is not None:
                found = builder
    return found


def _unpack_assign(node: ast.AST) -> tuple[list[ast.expr] | None, ast.expr | None]:
    """Return ``(targets, value)`` for both ``Assign`` and ``AnnAssign``."""
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value
    return None, None


def _builder_name_from_call(value: ast.AST) -> str | None:
    """``build_xxx_messages(...)`` -> ``"build_xxx_messages"``.

    Also handles ``await build_async_xxx(...)`` because cc-project's
    agents use async builders in a couple of places. Returns ``None``
    for method calls or expressions we don't try to resolve.
    """
    if isinstance(value, ast.Await):
        value = value.value
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id
    return None


# ---------------------------------------------------------------------------
# File / AST caching (shared between both passes)
# ---------------------------------------------------------------------------


def _get_tree(cache: dict[str, ast.Module], file_path: Path) -> ast.Module | None:
    """Parse *file_path* once per pass and cache the result. Errors fall
    through to ``None`` so the pass skips the file silently — the main
    scan already recorded a ``ScanWarning`` for unparseable files.
    """
    cached = cache.get(file_path.as_posix())
    if cached is not None:
        return cached
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return None
    cache[file_path.as_posix()] = tree
    return tree


def _relative(file_path: Path, project_root: Path) -> str:
    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()
