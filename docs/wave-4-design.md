# Wave 4 — Self-iteration loop (M4) — Design

**Status**: **Fully implemented and shipped in 0.1.0a3** (2026-05-23). All five decisions, all 7 worktrees (#24–#30 prereq + judge + critic + impact + loop + iterations-store + api-iterate + ui-iterate via #31) merged. This doc is preserved as the historical design record; it is not a TODO list.
**Source**: 5 design decisions agreed during the 0.1.0a2 / 0.1.0a3 transition.
**Predecessor**: Wave 3 shipped `iterate/__init__.py:iterate_one_round` as a single-round stub. Wave 4 replaces it with the full critique-and-revise loop.

---

## Scope

`src/aitap/iterate/` grows from one stub file to four modules:

- `judge.py` — LLM-as-judge: scores prompt outputs along configurable dimensions
- `critic.py` — Critique-and-revise: turns low scores + feedback into a new prompt version
- `impact.py` — Impact analyzer: detects downstream consumers of an iterated node
- `loop.py` — Full orchestrator: dataset → run → judge → critic → revise → re-run → score → converge or pause

Plus supporting work:

- New `iterations` table + DAO (`store/iterations.py`)
- Per-case run outputs persistence (`.aitap/runs/<id>/outputs.jsonl` — M3 carry-over)
- `scores` table actually populated (M3 schema exists; no writer until judge ships)
- UI: Auto-iterate panel + downstream-impact banner

## Out of scope (this Wave)

- M5 image-prompt grid (vision-based judge is a different concern)
- v0.2 multilingual scan
- Meta-iteration (using judge to iterate the judge prompt) — possible follow-up
- Multi-judge ensemble — follow-up
- External evaluator integration (pytest/ruff/sql-validate as judges) — follow-up

---

## Architecture flow

```
                ┌──────────────────────────────────┐
                │     existing M3 surface          │
                │  POST /api/runs                  │
                │  → playground.dispatch.invoke_run│
                │     → outputs.jsonl sidecar      │
                └────────────┬─────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────────┐
        │   iterate/loop.py  (orchestrator)          │
        │                                            │
        │   round = 1  ──► run baseline ──┐          │
        │   round n+1 ──► critic.revise ──┤          │
        │                                  ▼         │
        │                  judge.score(outputs)      │
        │                       │                    │
        │                       ▼                    │
        │             persist iteration row          │
        │                       │                    │
        │                       ▼                    │
        │             check convergence              │
        │             (delta / stagnation / max)     │
        │                       │                    │
        │           ┌───────────┴────────────┐       │
        │           ▼                        ▼       │
        │      converge: stop          continue loop │
        └───────────┬────────────────────────────────┘
                    │
                    ▼
        ┌────────────────────────────────────────────┐
        │   impact.analyze(prompt, new_version)      │
        │   → list downstream nodes                  │
        │   → UI banner / CLI warn                   │
        │   → user opts in to re-run subset/all      │
        └────────────────────────────────────────────┘
```

---

## Decision 1 — Judge: multi-dimensional, user-overridable

### Default dimensions

| Name | Weight | Rubric |
|---|---|---|
| `accuracy` | 0.40 | Is the output factually / logically correct? |
| `relevance` | 0.30 | Does it answer the actual question / task? |
| `safety` | 0.15 | Is it free of leakage, bias, harmful content? |
| `format` | 0.15 | Does it match the required structure (JSON schema, length, sections)? |

Total = weighted sum. Individual scores also persisted so the UI can render a radar chart and the critic can target weak dimensions.

### Override mechanism

Two scopes, second overrides first:

1. **Project-level** in `.aitap/config.yaml`:
   ```yaml
   judge:
     dimensions:
       - {name: accuracy,  weight: 0.5, rubric: "..."}
       - {name: citations, weight: 0.3, rubric: "All claims must have inline citations"}
       - {name: tone,      weight: 0.2, rubric: "Professional but warm"}
   ```

2. **Per-prompt** in `.aitap/prompts/<id>.prompt.yaml`:
   ```yaml
   judge_dimensions:
     - {name: sql_validity, weight: 0.6, rubric: "Generated SQL runs against the schema without error"}
     - {name: accuracy,     weight: 0.4, rubric: "..."}
   ```

UI: Settings page exposes the project-level editor; each prompt detail page can opt into an override.

### Why multi-dim, not a single score

Single score gives the critic no direction — "score is low, change something." Multi-dim lets the critic do **targeted edits**:
- `accuracy=0.3, format=1.0` → add factual constraints, leave format instructions alone
- `safety=0.5` → add refusal pattern, don't touch task instructions

Costs slightly more judge prompt engineering. Pays back in critic precision.

### Aggregation

```python
weighted_total = sum(score_i * weight_i for i in dimensions)
```

`weighted_total` ∈ [0, 1] is the canonical score persisted on each iteration. Per-dim breakdown saved alongside as JSON for analytics.

---

## Decision 2 — Revise: three modes, single interface

```python
def revise(
    prompt: PromptVersion,
    feedback: AggregatedFeedback,
    *,
    mode: Literal["auto", "guided", "manual"],
    instruction: str | None = None,  # required for "guided", ignored for others
    manual_text: str | None = None,  # required for "manual", ignored for others
) -> RevisedPrompt:
    ...
```

| Mode | Behaviour | Inputs the user provides |
|---|---|---|
| `auto` | Critic LLM reads feedback + scores, rewrites prompt freely | Nothing — fully automatic |
| `guided` | Critic LLM rewrites in a user-specified direction | Free-text instruction (e.g. "make the tone more professional", "shorten by 30%") |
| `manual` | No LLM call; user provides the new prompt verbatim | Full prompt text via UI editor |

All three modes write the same `prompt_versions` row + `iterations` row, so the convergence loop and the history view treat them uniformly. The mode is recorded on the `iterations` row (`revise_mode` column).

**UI**: a three-state toggle next to the Auto-iterate button. `manual` opens the same prompt editor the Playground already has; `guided` adds a single text input.

---

## Decision 3 — Convergence: baseline-relative, not absolute thresholds

### Default convergence

The loop stops when **any** of these fires:

```python
ConvergenceConfig(
    max_rounds=5,
    delta_from_baseline=0.15,      # round N total - baseline total
    stagnation_window=3,
    stagnation_epsilon=0.02,        # consecutive rounds with delta < eps
    # absolute_threshold=None       # off by default
)
```

### Baseline is first-class

Round 1 is **always** a baseline run: the unmodified prompt against the dataset. Its `weighted_total` is the reference point for all subsequent deltas.

Persisted via `iterations.is_baseline = TRUE` for round 1; absent (`FALSE`) for all later rounds.

### Why relative, not absolute

Absolute thresholds (`min_score=0.85`) look intuitive but they're load-bearing on three fragile assumptions:

1. **Judge prompt stability** — change one word in the judge prompt and the absolute score shifts. Relative deltas are invariant under judge rewording.
2. **Task uniformity** — a "summarise email" task and a "explain legal clause" task have different score ceilings. An absolute threshold that works for one is unreachable for the other; `delta >= 0.15` is fair to both.
3. **Dataset stability** — adding a harder test case to the dataset drops the absolute score even when prompt quality didn't change.

`delta_from_baseline` and `stagnation` are robust to all three. **Absolute thresholds are exposed as an opt-in advanced setting** but never the default.

### Per-dimension thresholds (advanced)

Optional override for users who care about a specific axis:

```yaml
convergence:
  delta_from_baseline: 0.15
  per_dim_thresholds:
    safety: 0.95       # safety must reach 0.95 absolute regardless of baseline
```

This is the **one** legitimate use of absolute thresholds — for non-negotiable axes like safety. Leave un-set in the default config.

---

## Decision 4 — Impact analyzer: warn-by-default, opt-in re-run

### Flow

1. User finishes iterating node `outline` (loop converges); new version commits to `prompt_versions` — **non-blocking**.
2. `impact.analyze(prompt_id, new_version)` walks the pipeline DAG from that node, returns the downstream consumers (`[draft, polish]` etc.).
3. The list is persisted on the iteration row as `downstream_status: {node_id: "unverified"}`.
4. UI shows a banner; CLI prints a warn. User picks:

| Action | Effect |
|---|---|
| **Skip** | Close banner; UI keeps a "⚠ N nodes unverified" badge on the prompt until manually addressed |
| **Re-run all** | Background job runs all downstream nodes against the dataset, compares to pre-iteration scores; updates `downstream_status` to `"verified"` or `"regressed"` |
| **Re-run selected** | Same as above but only for the checkboxed subset |

### Why warn-by-default

Auto-re-running downstream multiplies LLM cost by `O(downstream_nodes × dataset_size)`. For typical iterations (a tone tweak, a format constraint) this is wasted spend — the downstream effect is small. For structural rewrites it matters, and that's exactly when the user is willing to pay. Default off, easy opt-in.

### CLI surface

```bash
aitap iterate <prompt>                       # loop → commit → banner
aitap iterate <prompt> --rerun-downstream    # loop → commit → auto re-run all downstream
aitap iterate <prompt> --rerun draft         # selective
```

### `downstream_status` state machine

```
unverified ──user runs──► verified (score within ε of pre)
                       └► regressed (score dropped > ε)
                       └► improved  (score rose > ε; rare, but worth tracking)
```

The `unverified` count is what the UI badge surfaces; `regressed` triggers a more prominent warning.

---

## Decision 5 — Persistence: new `iterations` table

### Schema

```sql
CREATE TABLE iterations (
    id TEXT PRIMARY KEY,                     -- ULID
    prompt_id TEXT NOT NULL REFERENCES prompts(id),
    round INTEGER NOT NULL,                  -- 1-indexed within a session
    session_id TEXT NOT NULL,                -- groups rounds of one /iterate invocation
    is_baseline INTEGER NOT NULL DEFAULT 0,  -- TRUE for round 1
    parent_version INTEGER,                  -- prompt_versions.version this round started from
    new_version INTEGER,                     -- prompt_versions.version produced (NULL for baseline)
    revise_mode TEXT,                        -- 'auto' | 'guided' | 'manual' | NULL for baseline
    revise_instruction TEXT,                 -- user instruction for 'guided'; NULL otherwise
    critique_text TEXT,                      -- judge's critique passed to critic
    weighted_score REAL NOT NULL,
    per_dim_scores TEXT NOT NULL,            -- JSON: {dim_name: score}
    downstream_status TEXT,                  -- JSON: {node_id: status} | NULL if not pipeline node
    converged_reason TEXT,                   -- 'max_rounds' | 'delta' | 'stagnation' | NULL while in progress
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    UNIQUE (prompt_id, session_id, round)
);

CREATE INDEX idx_iterations_prompt ON iterations(prompt_id);
CREATE INDEX idx_iterations_session ON iterations(session_id);
```

### Why a new table, not extend `prompt_versions`

`prompt_versions` is a **flat version log** — one row per version, immutable. An iteration is an **event with mutable state** (downstream_status updates after a re-run hours later). Forcing iteration fields into `prompt_versions` would:

- Pollute the version log with rows whose meaning changes after the fact
- Complicate the `aitap rollback` / `aitap diff` semantics ("rolling back to a half-verified iteration?")
- Block clean M5 extension (image iteration adds new attachment fields, would balloon `prompt_versions` further)

The new table is a clean event log with foreign keys back to `prompts` and `prompt_versions`. History UI does two queries (versions + iterations) and overlays them; the SQL is simple either way.

### DAO sketch (`store/iterations.py`)

```python
def new_session_id() -> str: ...
def insert_iteration(conn, *, ...) -> str: ...   # returns ULID
def update_downstream_status(conn, iteration_id, node_id, status) -> None: ...
def read_session(conn, session_id) -> list[Iteration]: ...
def latest_iteration_for(conn, prompt_id) -> Iteration | None: ...
def read_iterations_for(conn, prompt_id, *, limit=50) -> list[Iteration]: ...
```

---

## Worktree breakdown — proposed

| # | Worktree | Module / files | Depends on | Notes |
|---|---|---|---|---|
| **P** | `wt/runs-persistence` | `playground/dispatch.py` extension + `.aitap/runs/<id>/outputs.jsonl` writer | (M3 main) | **Wave 4 prerequisite** — must merge before judge can score |
| 1 | `wt/judge` | `iterate/judge.py`, default dimensions, config loader | runs-persistence | Includes scoring DAO writes to existing `scores` table |
| 2 | `wt/critic` | `iterate/critic.py`, three revise modes | judge (consumes weighted_score + per_dim_scores) | Auto + guided + manual interface |
| 3 | `wt/impact` | `iterate/impact.py`, DAG walker, status tracker | scanner.models.Pipeline (existing) | Pure analysis; no LLM calls in this worktree |
| 4 | `wt/loop` | `iterate/loop.py`, `ConvergenceConfig`, session ID generator | judge + critic + impact + iterations DAO | Replaces M3's `iterate_one_round` stub |
| 5 | `wt/iterations-store` | `store/iterations.py` + DDL migration | (M3 store.db.py CONTRACT — needs protocol mention) | Can land in parallel with judge |
| 6 | `wt/api-iterate` | `server/routes/iterate.py` extension; new endpoints for session, status, re-run | loop + iterations-store | Existing `POST /api/runs/{id}/iterate` becomes session start |
| 7 | `wt/ui-iterate` | UI Auto-iterate panel, mode toggle (auto/guided/manual), convergence config UI, downstream banner | api-iterate | Touches Playground + History pages — coordinate with M3 owners |

### Suggested order

1. **wt/runs-persistence** lands first (prerequisite, small).
2. **wt/iterations-store** + **wt/judge** + **wt/impact** in parallel (independent).
3. **wt/critic** after judge merges (needs the per_dim score shape).
4. **wt/loop** ties 1-3 together; depends on all preceding.
5. **wt/api-iterate** depends on loop.
6. **wt/ui-iterate** last; consumes the API.

Total: 1 prerequisite + 6 main worktrees. Roughly comparable to Wave 3's scope.

---

## Open questions / future work

- Meta-iteration: using judge to iterate the judge prompt itself. Not in Wave 4 — requires a separate convergence story.
- Multi-judge ensemble: aggregate scores from N judges (different models) to reduce per-judge bias. Cost trade-off makes this a v0.2 candidate.
- Non-LLM evaluators: hook `pytest` / `ruff` / external scripts as judges for code-generation prompts. Schema is forward-compatible; just needs a `judge_type` discriminator on the dimension config.
- Image-prompt judging: vision-capable judge. M5 lands the runner; M5+1 can lift judge into vision.

---

## References

- `docs/architecture.md` §4 Iteration loop — original sketch of the critique→revise→re-run→score loop
- `WORKTREES.md` L862 — Wave 3 → Wave 4 sync gate
- `CONTRACTS.md` — `iterate/judge.py` and `iterate/critic.py` listed as consumers of `LLMClient`
- Existing M3 stub: `src/aitap/iterate/__init__.py:iterate_one_round` — to be replaced by `wt/loop`'s full implementation
