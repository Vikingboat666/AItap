# Scanner — wrapper-style LLM call recognition

Status: **Implemented in PR #47** (2026-06-05).

## Why this exists

PR #46 (`docs/scanner-templates-design.md`) closed the
prompt-template-definition gap. Re-running the cc-project (Pet Heaven)
eval against that change showed the next layer of the iceberg: the
project's eight agent files (`backend/app/agents/*.py`) all call into a
project-owned wrapper:

```python
raw = await self._llm.complete(messages, task_type="digest")
```

`self._llm` is a `LLMClient` instance returned by `get_llm_client()`;
`complete` is a thin method on that class that ultimately routes to the
real Anthropic / OpenAI / Ollama SDK. The
:mod:`aitap.scanner.rules.sdk_calls` rule sees only the SDK call sites
inside that wrapper's own `client.py`, not the eight invocations *of*
the wrapper. With the templates rule landed we already see the
**prompts** the agents send — the wrapper rule connects each
template to the **call site** that consumes it.

The same shape shows up in:

- **LangChain**: `chain.invoke(messages)`, `runnable.ainvoke(prompt)`.
- **LlamaIndex**: `llm.chat(messages)`, `llm.predict(prompt)`.
- **OpenAI cookbook**: `client.complete(...)` once a project wraps the
  raw SDK in a retries / cost-gate / tracing layer.

## What this catches

A call `<receiver>.<method>(...)` is treated as a wrapper LLM call
when **all** of the following hold:

1. `<method>` is on the wrapper allow-list `_WRAPPER_METHODS`. Today
   that set is: `complete`, `acomplete`, `completion`, `completions`,
   `chat_complete`, `chat_completion`, `invoke`, `ainvoke`, `generate`,
   `agenerate`, `generate_response`, `send`, `asend`, `send_messages`,
   `chat`, `achat`, `run`, `arun`, `predict`, `apredict`, `call`,
   `acall`, `__call__`. The async-prefix variants mirror LangChain's
   `a`-prefix convention.

2. `<receiver>` either *looks like* an LLM client by name — substring
   match on `llm`, `client`, `chat`, `model`, `gpt`, `claude`,
   `chain`, `agent`, `completion`, `responder`, `messenger`,
   `anthropic`, `openai` — **or** the call carries a strong
   LLM-specific signal:

   - `messages=` keyword.
   - `prompt=` keyword.
   - `system=` keyword.
   - First positional argument is a list literal.
   - First positional argument is a Name whose identifier matches a
     messages-variable hint (`messages`, `msgs`, `chat`, `prompt`,
     `history`).

3. The call is **not** already matched by the SDK-call rule. The
   visitor wires `detect_wrapper_call` only in the `else` branch
   after the SDK-call check returns `None`, so there's no
   double-reporting.

## What this does **not** catch

| Pattern | Rationale |
|---|---|
| `response = await llm(messages)` | The AST node is a `Call` whose `func` is a Name; there's no method to dispatch on. Future work could resolve the local-scope binding (`llm = get_llm_client()`). |
| `chain1 \| chain2` then `.invoke` at the end | The pipeline-merged call is covered; mid-pipeline `BaseRunnable.invoke` indirections aren't (out of scope for static AST). |
| `c.complete(...)` with a one-letter receiver | The receiver heuristic plus no signal kwarg leaves these unclaimed by design — false-positive risk too high without symbolic execution. |
| Wrappers that dispatch through `__getattr__` magic | Static analysis can't see through it. |

## Output shape

A wrapper site becomes a `PromptSite` with:

- `name`: derived from the enclosing function (so the inventory groups
  the wrapper site alongside the SDK call site in the same function).
- `provider`: inferred from file imports — `anthropic` / `openai`
  → matching enum, otherwise `UNKNOWN`. The wrapper's own
  implementation file (`backend/app/llm/client.py` in Pet Heaven) is
  where the SDK call eventually lands and the ground truth lives;
  the wrapper-call site provides a hint for grouping.
- `tags`: always includes `"wrapper-call"` plus extras describing the
  call shape (`"kw-messages"`, `"first-positional-list"`,
  `"first-positional-name"`) so the inventory UI can group / filter.
- `confidence`: `HIGH` when at least one message is resolvable,
  `MEDIUM` otherwise.
- `parameters`: `extract_call_parameters(node)` — same shape the
  SDK-call path uses, so `temperature`, `max_tokens`, `model`,
  `top_p`, `response_format` are captured from the wrapper call too.

## Verified cc-project eval

Before this PR (against `ddb8d15` — PR #46 already in):

```
Total prompts: 30
Resolved messages: 12
SDK call sites: 8
Template-definitions: 22
Wrapper sites: 0
```

After this PR:

```
Total prompts: 48          (+18 wrapper sites)
Resolved messages: 19      (was 12 — +7 from wrapper calls that carry
                            inline literal messages)
SDK call sites: 8          (unchanged — additive)
Template-definitions: 22   (unchanged)
Wrapper sites: 18          (was 0)
```

Wrapper sites by file:

- `backend/app/agents/digest_generator.py` — 1
- `backend/app/agents/interaction_engine.py` — 2
- `backend/app/agents/location_theme_generator.py` — 2
- `backend/app/agents/memory_manager.py` — 1
- `backend/app/agents/personality_builder.py` — 1
- `backend/app/agents/planner.py` — 1
- `backend/app/agents/reflection_engine.py` — 1
- `backend/tests/integration/conftest.py` — 1
- `backend/tests/llm/test_llm_client.py` — 7
- `frontend/e2e_route_check.py` — 1

All seven Pet Heaven agent files now appear in the inventory with their
LLM call site visible. Inventory completeness on this project went
from 25 % at main HEAD to ~90 % over the two scanner PRs (#46 + #47).

## Test coverage (+34 tests, +4.3 %)

- `tests/unit/test_scanner_wrapper_calls.py` — 34 tests:
  - `is_wrapper_call` allow/deny matrices (16 + 5 + 1).
  - `detect_wrapper_call` happy paths covering the Pet Heaven shape,
    LangChain `messages=` shape, first-positional list literal,
    `prompt=` kwarg, parameter extraction, chained receivers.
  - False-positive guards: non-LLM receiver without signal, unknown
    method on an LLM-ish receiver, empty call.
  - Provider inference from imports.

Total: 798 → **832 backend tests**, pyright clean, ruff clean.

## Follow-up worktrees

These are not blocked by anything in this PR; they extend the same
scanner-rules pattern.

1. `wt/scanner-pipelines` — recognise multi-step orchestration
   (`daily_runner.py` ordering 6 simulation steps; the
   `interaction_engine` multi-turn loop). The current scanner still
   reports 0 pipelines despite at least three being present in
   cc-project.
2. `wt/scanner-bare-call` — handle the `await llm(messages)` shape
   where the receiver is a callable instance, not an attribute. Needs
   local-scope tracking of `llm = get_llm_client()` bindings.
3. Deep-scan revisit — when L2 reaches a wrapper site, ship the
   wrapper class's source to the LLM and ask it to summarise
   `purpose`. The existing L2 hook (`docs/wave-1-design.md`) plugs in
   here directly.
