# Scanner — template-definition recognition

Status: **Implemented in PR #46** (2026-06-04).

## Why this exists

The cc-project (Pet Heaven) eval surfaced a structural gap in L1 scanning:
the previous rules only matched *SDK call sites*
(`client.chat.completions.create(...)`, `client.messages.create(...)`),
which is the right shape for `requests.post`-style projects but misses
the layout production projects converge on.

In practice, projects of any size pull their prompts out of the SDK call
site and into a dedicated module:

- `app/llm/prompt_templates.py` — Pet Heaven, 9 `build_<task>_messages`
  helpers plus `HEAVEN_WORLD_RULES`.
- `chains/system.py` — LangChain-style applications.
- `prompts.py` — OpenAI-cookbook idiom.

The SDK-call rule sees `client.messages.create(messages=foo)` but never
`foo`. The cc-project eval ran a 30-template project through the scanner
and got back 8 sites with all message texts empty — a 25 % recall on
"prompt sites the user can name" and a 0 % recall on "prompt text the
user can read".

This PR adds two new rule families that close the gap.

## What this catches

Two patterns, both syntactic and AST-only — no constant folding, no
cross-file resolution, no symbolic execution.

### 1. Builder functions

Functions whose name matches `^(build|make|compose|render|format|create|get|_build)_<task>_(messages|prompt|chat|template|instructions?)s?$`
and whose body returns a `list[dict[str, str]]` shaped like the canonical
OpenAI `messages` payload.

Examples covered: `build_personality_messages`, `make_chat_prompt`,
`compose_dialog_chat`, `render_grading_template`,
`_build_internal_messages`.

Two return shapes are parsed:

- Direct: `return [{"role": "system", "content": "..."}, ...]`
- Named: `messages = [...]; return messages` (one canonical assignment
  to the returned name)

A name-matched builder with an opaque body (e.g. composes the messages
with `dedent(f"...")` calls, or builds them under branching) still emits
a `PromptSite` with `template_kind=UNRESOLVED`. The site exists so the
operator can surface the file as worth a look; the deep-scan path can
resolve text later.

### 2. Module-level prompt constants

Top-level assignments whose name matches `^(SYSTEM|USER|PROMPT|TEMPLATE
|INSTRUCTIONS?|HEAVEN|RULES|RUBRIC|CRITIC|JUDGE|PERSONA)_…$` or
`^…_(PROMPT|TEMPLATE|INSTRUCTIONS?|RULES|RUBRIC)$` and whose RHS is a
string literal — triple-quoted, `textwrap.dedent`-wrapped, plain, or
f-string.

Examples covered: `SYSTEM_PROMPT`, `HEAVEN_WORLD_RULES`,
`COMPANION_PROMPT`, `REUNION_TEMPLATE`, `JUDGE_INSTRUCTIONS`.

Role inference from the name prefix:
- `SYSTEM_*` → `system`
- `ASSISTANT_*` → `assistant`
- `TOOL_*` → `tool`
- everything else → `user` (safest for free-floating prompt bodies)

## What this does **not** catch

| Pattern | Rationale |
|---|---|
| Wrapper-style calls (`self._llm.complete(...)` on a project-owned client) | Requires resolving the wrapper class to know whether it ends in an SDK call. A future `wt/scanner-wrappers` worktree handles this; the design doc opens with the wrapper grep evidence so the case is on the board. |
| Cross-file constant refs (`messages=PROMPTS["intro"]`) | Symbolic execution territory — deep-scan job. |
| Builder functions whose name doesn't match the pattern | The name allow-list is deliberately narrow. False positives on `build_response` / `format_log_line` would drown the inventory. |
| Function-local `PROMPT_X = ...` | Scratch variables, not template definitions. |
| Method-on-class `build_messages` | Methods are wrapped by their class context (a runner / executor); the SDK-call path inside the method is the right surface. |

## Output shape

A detected template definition becomes a `PromptSite` carrying:

- `name`: the identifier (preserved case — `SYSTEM_PROMPT`, not `system_prompt`).
- `provider`: inferred from file imports (`anthropic` / `openai`); falls
  back to `UNKNOWN` when the module is provider-agnostic (typical for a
  shared `prompts.py`). The SDK-call site, when it lands, has the
  ground-truth `provider`; this Provider is a grouping hint.
- `tags`: always includes `"template-definition"` plus one of
  `"builder-function"` / `"module-constant"` so the UI can render
  these distinctly from SDK call sites.
- `confidence`: `HIGH` when at least one message has a resolvable
  template, `MEDIUM` otherwise (mirrors `prompt_extractor._confidence_for`).
- `parameters`: empty `CallParameters()` (a definition has no
  `temperature` / `max_tokens` — those live on the call site).

## Verified cc-project eval

Before this PR (against `c8135ae`):

```
Total prompts: 8
Resolved messages: 0
SDK call sites: 8
Template-definitions: 0
```

After this PR:

```
Total prompts: 30          (+275 %)
Resolved messages: 12      (was 0 — 40 % of message slots now carry text)
SDK call sites: 8          (unchanged — additive change)
Template-definitions: 22
Files with at least one prompt: 8
```

Newly discovered surfaces include:

- `app/llm/prompt_templates.py` — `HEAVEN_WORLD_RULES` + 9
  `build_<task>_messages` helpers (the central prompt asset the project
  owner names in `CLAUDE.md`).
- `scripts/pixel_*.py` — five PROMPT / TEMPLATE constants, two of which
  resolve to literal text the UI can show.
- `scripts/test_llm_concurrency.py` — `SHORT_PROMPT` literal.

## Test coverage (+69 tests, +9.5 %)

- `tests/unit/test_scanner_template_definitions.py` — 58 tests:
  - Name-pattern allow/deny matrices (15 + 8 + 12 + 5).
  - `detect_builder_function` happy paths (literal return, named
    assignment then return, async def) + degradation + provider
    inference.
  - `detect_prompt_constant` happy paths (plain / triple-quoted /
    f-string) + role inference + dynamic-RHS degradation + provider
    inference.
- `tests/unit/test_scanner_python_template_integration.py` — 11 tests
  exercising the full `scan_python_file` pipeline against fabricated
  fixture sources (Pet Heaven / LangChain shapes), including the
  scope-discipline guarantees (no nested, no class methods, no
  function-local).

Total: 729 → **798 backend tests**, pyright clean, ruff clean.

## Follow-up worktrees

These are not blocked by anything in this PR; they extend the same
scanner-rules pattern.

1. ✅ `wt/scanner-wrappers` (PR #47) — recognised project-owned
   wrapper clients (`self._llm.complete(...)`, `BaseLLM.invoke(...)`,
   LangChain `chains.invoke`). The cc-project agents matched all 8.
2. ✅ `wt/scanner-link-builder` (PR #56) — closed the wrapper-call
   site's UNRESOLVED-text gap by walking the enclosing function for
   `messages = build_xxx(...)` and copying the matching builder's
   resolved messages onto the wrapper. A second sibling pass
   deepens builder-body resolution (Name + helper-call + module-const
   chains, up to four hops) so 8/8 cc-project builders now resolve
   fully and the linked wrappers carry the same text. See
   `src/aitap/scanner/rules/cross_call_resolution.py`.
3. `wt/scanner-pipelines` — recognise multi-step orchestration
   (`daily_runner.py` ordering 6 simulation steps; the
   `interaction_engine` multi-turn loop). The current scanner reports
   0 pipelines on cc-project despite at least three being present.
4. Deep-scan revisit — when L2 reaches a builder with an UNRESOLVED
   body, ship the function source to the LLM and ask it to summarise
   `purpose`. The existing L2 hook (`docs/wave-1-design.md`) plugs in
   here directly.
