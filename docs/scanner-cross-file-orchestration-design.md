# Scanner — cross-file orchestration recognition

Status: **Implemented.** Two rules now live in this design space:

- **PR #51 (2026-06-05)** — original cross-file rule. Resolves
  `self.<attr>.<method>(...)` where `<attr>` points through `__init__`
  to a class defined in another LLM-bearing file. Verified against
  cc-project: pipelines went from **0** to **1** (`plan_day_pipeline`,
  4 nodes / 3 edges, anchored by `DailyRunner.run`).
- **PR #73 (B1, `wt/scanner-pipelines`)** — same-class sibling rule
  (`IntraClassMethodChain` in
  `src/aitap/scanner/dataflow/intra_class_method_chain.py`). Resolves
  `self.<method>(...)` where `<method>` is a method of the *same*
  class whose body contains an LLM PromptSite. Covers the multi-turn
  engine shape (`interaction_engine`'s `classify_intent` →
  `generate_response` → `validate` …) that PR #51's rule can't see
  because there's no `<attr>` hop. Threshold: ≥3 distinct LLM-bearing
  steps per orchestrator method (below that we'd overlap with the
  existing free-function helper detectors). Confidence: MEDIUM.
  Same "do NOT" list applies — no agent-like hints, no method-name
  heuristics; resolution is purely syntactic
  (`self.<name>` bound-method calls + per-method PromptSite anchor).

## The problem

The cc-project (Pet Heaven) eval after PRs #46 + #47 + #48 + #49 hits
~90 % inventory completeness on the prompt-site axis but still reports
**zero pipelines** even though the project clearly has at least three
multi-step LLM orchestrations:

1. `backend/app/simulation/daily_runner.py::DailyRunner.run` sequences
   six steps:
   ```python
   plan   = await self._planner.plan_day(pet, …)
   pairs  = self._matcher.match(world)
   scene  = await self._engine.run_interaction(pair, …)
   _      = await self._reflector.reflect(pet, …)
   _      = await self._digest_gen.generate(pet, …)
   _      = await self._rel_updater.recalculate_all(world, …)
   ```
2. `backend/app/simulation/world_state.py` schedules
   `LocationThemeGenerator.generate(loc, db)` for every location.
3. `backend/app/agents/interaction_engine.py::run_interaction`
   coordinates `_get_or_create_relationship` →
   `_get_shared_memories` → `_get_pet_item_names` → `self._llm.complete`
   → `_save_memories` → `_update_relationship` →
   `_handle_discoveries` → `_handle_item_exchange`.

The existing dataflow detectors (`IntraFileChain`, `LangChainPipe`,
`LlamaIndexEngine`, `VariableTracker`) all operate intra-file with at
least two prompt sites in the same file. cc-project violates both
assumptions:

- Most agent files host **one** wrapper site each; intra-file
  detection sees nothing to chain.
- The orchestrator file (`daily_runner.py`) holds **zero** LLM sites
  itself — every step is a method call on an attribute whose class
  lives in a separate `app/agents/*.py` file.

## The attempted shortcut and why it was reverted

`wt/scanner-pipelines` (PR not opened) explored a syntactic
"orchestration site" rule: flag any function whose body has ≥ N
distinct `<receiver>.<method>(...)` calls. Two thresholds were tried:

- **N=3 without a receiver-name gate**: 254 of 302 sites flagged.
  Every Alembic migration's `upgrade`, every `test_xxx` calling
  `self.client.post(...)` thrice, every API handler chaining DB
  helpers got claimed. Useless.
- **N=3 with the "agent-like receiver hint" gate** (`agent`, `chain`,
  `engine`, `pipeline`, `runner`, `executor`, `workflow`, `graph`,
  `crew`, `orchestrator`): only 3 sites flagged. All three were unit
  tests mocking `engine._llm.complete`, not real product
  orchestrators. `daily_runner.run` still missed because its receivers
  use `_planner` / `_matcher` / `_reflector` / `_digest_gen` /
  `_rel_updater` naming — only `_engine` matches the hint list, and
  the gate requires ≥ 2 hint matches.

Lowering the gate makes recall worse (more noise). Tightening the hint
list makes recall worse (more misses). The signal is genuinely not in
the orchestrator file's syntax.

## What recognising cross-file orchestration actually needs

A correct rule has to do at least one of:

1. **Resolve `from app.agents.planner import Planner` and the
   attribute assignment `self._planner = Planner()` to the class
   defined elsewhere**, then check whether *that* class has a wrapper
   or SDK site. This is real cross-file dataflow tracking; the
   existing detectors deliberately scoped out.
2. **Ship a runtime trace path** — instrument the project's actual
   `daily_runner.run` and observe which agent methods get called. Out
   of scope for L1; a future runtime-trace layer could feed this back.
3. **Accept user annotation** — let the project author drop a
   `# aitap-pipeline: daily-runner` comment / decorator that aitap
   picks up directly. Fastest precise path, but adds a project-side
   contract.

## Recommended approach for the dedicated worktree

A dedicated `wt/scanner-cross-file-orchestration` worktree should:

- Add an AST pass that resolves `self.<attr>` assignments inside each
  class's `__init__` (or class-level annotations) to the class
  imported via `from <module> import <Class>`.
- Build a per-class "has at least one LLM site" map from the already-
  computed prompt sites — a class qualifies when at least one of its
  methods produces a wrapper / SDK / template-definition site.
- For each function in the project, count distinct
  `self.<attr>.<method>(...)` calls whose `self.<attr>` resolves to an
  LLM-bearing class. ≥ 2 such calls in source order → a
  cross-file orchestration site.
- Emit either a `pipeline-orchestration` site on the orchestrator
  function or, more honestly, a proper `Pipeline` value with edges
  pointing at the prompt sites the wrapped classes hold. The latter
  fits the existing `ScanResult.pipelines` shape directly.

This is a 400 – 600 line worktree on its own; doing it correctly is a
better engineering investment than landing a heuristic that flags 254
false positives or a stricter heuristic that only catches three unit
tests.

## What this draft asks the next worktree to *not* do

- Don't add new "agent-like" receiver hints. The vocabulary cc-project
  uses (`planner`, `matcher`, `reflector`, `gen`, `updater`) is not
  framework vocabulary; adding it would over-fit one sample again,
  exactly the trap PR #48 already had to fix.
- Don't lower the per-function step threshold below 3 without a
  cross-file signal. The unit-test false positives surface at
  threshold 3; threshold 2 turns the whole test directory into
  apparent pipelines.
- Don't add a "method name" heuristic (`plan`, `reflect`, `generate`).
  Same over-fit risk; common business code uses these terms too.

## References

- PR #46 (`docs/scanner-templates-design.md`) — template-definition
  recognition.
- PR #47 (`docs/scanner-wrappers-design.md`) — wrapper-call
  recognition.
- PR #48 (`tests/unit/test_scanner_generality.py`) — generality test
  suite that PR #46 / #47 had to bolt on once we tried this rule
  family.
- `src/aitap/scanner/dataflow/intra_file_chain.py` — existing
  intra-file chain detector, useful baseline for the cross-file
  worktree to extend.
