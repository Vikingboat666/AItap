# Wave 5 — M5 — Design

Status: **Partial.** Both contract-shaping decisions (A·D1, B·D1) are signed off.

- **Part A — pipeline segment mode UI:** ✅ **implemented and merged** in PR #32 (segment-dispatch backend) + PR #33 (segment-ui frontend), 2026-05-24.
- **Part B — image-prompt grid view:** ⏳ **not yet implemented.** Three worktrees still on the backlog: `wt/image-client` → `wt/image-dispatch` → `wt/image-ui`. Multi-provider redesign (`docs/profiles-design.md`) is being completed first; image grid resumes after that.

M5 ships two unrelated features. They are sequenced by priority, not coupling:

1. **Pipeline `segment` mode UI** (priority 1) — expose the contiguous-slice runner that already exists in the backend but is unreachable from the product.
2. **Image-prompt grid view** (priority 2) — a brand-new surface for visually comparing text-to-image prompt outputs.

Release target: **0.1.0a4**.

---

## Why segment-first

| | Segment mode | Image grid |
|---|---|---|
| Runner | ✅ `run_pipeline` already supports `node`/`segment`/`end_to_end` | ❌ none — `LLMClient` only does `chat()` |
| Provider / pricing | ✅ reuses existing chat providers | ❌ no image provider, no image pricing table |
| API contract | ⚠️ `RunCreate.pipeline_segment` exists; **dispatch ignores it** | ⚠️ `RunOutput.image_path` reserved only |
| UI | ⚠️ `DagView.onNodeClick` exists; selector hides `segment` | ❌ greenfield |
| Net shape | **wire up + UI** | **build a new vertical from scratch** |

Segment mode is mostly *connecting wires that already exist*; image grid is *laying new track*. Doing segment first lands user value fast and de-risks the larger image effort.

---

## Scope (this Wave)

- Make pipeline `segment` mode (and, incidentally, `node` mode) actually dispatch to the runner end-to-end, driven by a node-pick UI on the DAG.
- A read-only image grid: generate N variants of an image prompt across M cases and render them in a grid for **manual** comparison.

## Out of scope (this Wave)

- **Vision-capable judge** — image outputs are not auto-scored in M5. The grid is for human eyeballing only. (`M5+1` lifts the judge into vision; see Open questions.)
- **Self-iteration of image prompts** — no judge means no convergence loop for images.
- Multi-provider image support beyond the first provider we wire.
- Animated / video generation.
- Editing/inpainting image flows.

---

## Current state — verified

### Segment mode

- **Runner is complete.** `playground/pipeline_runner.py`:
  - `PipelineMode = Literal["node", "segment", "end_to_end"]`
  - `run_pipeline(pipeline, mode, *, dataset_cases, site_index, client, parameters, version=1, node_id=None, segment=None)`
  - Kahn topo-sort restricted to a node subset (`_topological_order`), cycle/dangling detection, per-case intermediates.
- **API contract is half-there.** `server/routes/__init__.py`:
  - `RunCreate.pipeline_segment: list[str] | None = None` exists.
  - There is **no** `mode` or `node_id` field.
  - `RunOutput` already carries `intermediate: dict[str, str] | None` and `image_path: str | None`.
- **Dispatch is the gap.** `playground/dispatch.py:invoke_run` hardcodes `"end_to_end"` for `target_kind == "pipeline"` and **never reads `payload.pipeline_segment`**. So today *every* pipeline run is end-to-end regardless of what the UI requests — the `node` vs `end-to-end` selector is currently cosmetic.
- **UI is half-there.** `pages/Playground.tsx`:
  - `type Mode = "node" | "segment" | "end-to-end"`, default `"node"`.
  - The selector renders only `["node", "end-to-end"]`; `segment` is deliberately omitted (comment: “until the node-pick UI lands (M5) … exposing it would let users dispatch a zero-node segment run”).
  - The run mutation already sends `pipeline_segment` **only** when `mode === "segment"`.
  - `pages/components/DagView.tsx` already exposes `onNodeClick?: (promptId: string) => void`.

### Image grid

Greenfield. The only pre-wiring is `RunOutput.image_path: str | None` and a structural mention of “image grid” in `docs/architecture.md`. `deep/client.py:LLMClient` has only `chat()` + `estimate_cost()`; no image-generation provider, no image pricing.

---

# Part A — Pipeline segment mode (priority 1)

## A·Decision 1 — Express run mode explicitly on the wire — ✅ decided

**Decision: an explicit, defaulted `pipeline_mode` field.** `RunCreate` today has only `pipeline_segment`; rather than overload that one field to mean three modes by inference (`None`/one-id/many-ids), we add an explicit mode. The inference alternative was rejected because it can’t distinguish an empty list (a bug) from “not provided”, and collides “run the whole pipeline as a segment” with “end-to-end”.

Additive change to the frozen `routes/__init__.py` contract:
```python
class RunCreate(_ApiModel):
    ...
    pipeline_mode: Literal["node", "segment", "end_to_end"] | None = None
    pipeline_node_id: str | None = None     # required when pipeline_mode == "node"
    pipeline_segment: list[str] | None = None  # required when pipeline_mode == "segment"
```
`None` defaults to `end_to_end`, preserving today’s exact behavior byte-for-byte — a backward-compatible additive contract change (follow the CONTRACTS.md additive protocol; broadcast to any open worktrees). This also makes the validation rules below expressible.

## A·Decision 2 — Node-pick UX on the DAG

- Clicking a node in `DagView` toggles its membership in the segment selection (the `onNodeClick` hook already exists). Selected nodes get a highlighted style; a live “segment: N nodes” counter shows in the run panel.
- Re-add `"segment"` to the Playground mode selector. When `mode === "segment"`, the run panel shows the DAG in pick mode and the Run button reads “run segment (N nodes)”.
- `node` mode reuses the same click affordance but caps selection at one node and sends `pipeline_node_id`.

## A·Decision 3 — Validation: no empty, warn on non-contiguous

- **Empty selection is blocked**, not silently run: Run is disabled while the segment set is empty (kills the “zero-node segment succeeds with empty output” footgun the Playground comment flags). The backend also rejects an empty `pipeline_segment` with a 422 rather than the runner’s “succeeds with no output”.
- **Non-contiguous selection warns but does not block.** The runner already feeds dataflow only along edges *within* the selected set; two disconnected islands run independently, which is a legitimate (if unusual) request. Surface a non-blocking “these nodes aren’t connected — they’ll run as independent groups” note.
- Cycle / dangling reference in the subset stays a hard runtime `ValueError` (already the runner’s behavior) → surfaced as a 422.

## A·Decision 4 — Wire dispatch to honor mode (also fixes the latent node-mode no-op)

`dispatch.invoke_run` stops hardcoding `"end_to_end"` and maps the contract fields to the runner:

```python
mode = payload.pipeline_mode or "end_to_end"
run_pipeline(
    pipeline, mode,
    node_id=payload.pipeline_node_id,
    segment=payload.pipeline_segment,
    ...
)
```

This is the single change that makes both `node` and `segment` modes real.

### Segment — backend changes
- `routes/__init__.py`: additive `pipeline_mode` + `pipeline_node_id` (A·D1).
- `routes/runs.py`: validate mode/field consistency (node needs `pipeline_node_id`; segment needs non-empty `pipeline_segment`); 422 on violation.
- `playground/dispatch.py`: route to `run_pipeline` with the requested mode (A·D4).

### Segment — frontend changes
- `DagView`: selection-highlight styling; multi-select toggle.
- `Playground.tsx`: re-add `"segment"` to the selector; pick-mode panel; counter; node/segment field wiring; disable Run on empty.
- Regenerate the API client (`pnpm gen:api`) after the contract change.

### Segment — testing
- Backend: dispatch routes each mode to the runner with the right args; runs route 422s on empty segment / missing node id; segment run produces per-node `intermediate`.
- Frontend: node click toggles selection; Run disabled when empty; correct `pipeline_mode`/`pipeline_segment` in the POST body; non-contiguous warning renders.

---

# Part B — Image-prompt grid (priority 2)

## B·Decision 1 — A separate `ImageClient` abstraction — ✅ decided

**Decision: a separate `ImageClient` ABC, not an extension of `LLMClient`.** Image generation has a different call shape than chat (prompt → N images, size/quality knobs, bytes out). Bolting `generate_image` onto `LLMClient` would force every chat provider (most of which can’t draw) to implement or stub it, polluting the chat contract for one new capability. Instead, add a parallel ABC + registry mirroring `deep/client.py`:

```python
class ImageClient(abc.ABC):
    @abc.abstractmethod
    async def generate(self, prompt: str, *, n: int, size: str) -> list[ImageResult]: ...
    @abc.abstractmethod
    def estimate_cost(self, *, n: int, size: str) -> CostEstimate: ...
```

Providers lazy-import their SDKs exactly like the chat providers. `MockImageClient` keeps the suite offline. This keeps the chat contract clean and lets image providers evolve independently; the contract risk is low because we add a brand-new interface rather than mutating the frozen `LLMClient`.

## B·Decision 2 — One provider to start

Wire a single image provider in M5 (proposed: **OpenAI `gpt-image-1`**), with the registry shape ready for more. Add image rows to the pricing table for cost estimation.

## B·Decision 3 — Storage

Generated images land at `.aitap/runs/<id>/images/<case_index>_<variant>.png`; the path is recorded in the already-reserved `RunOutput.image_path`. The outputs sidecar carries the relative path, not the bytes.

## B·Decision 4 — Cost confirmation gate

Image generation is materially pricier than a chat call. Reuse the L2-scan cost-confirmation pattern: estimate `n_variants × n_cases × price`, confirm before spending, `--yes`/explicit-confirm to skip in scripted use.

## B·Decision 5 — Grid UI, manual eval only

- An `N variants × M cases` grid; click a cell to enlarge; show the prompt + seed/params per column.
- **No scoring widgets.** Auto-scoring requires a vision judge, which is M5+1. M5 is human-in-the-loop comparison only.

### Image — testing
- `MockImageClient` drives provider-agnostic tests; cost-gate math; sidecar path persistence; grid renders an k×m matrix; enlarge interaction.

---

## Worktree breakdown — proposed

| # | Worktree | Module / files | Depends on | Notes |
|---|---|---|---|---|
| **1** | `wt/segment-dispatch` | `routes/__init__.py` (+`pipeline_mode`/`pipeline_node_id`), `routes/runs.py` validation, `playground/dispatch.py` wiring | M4 main | **Priority 1.** CONTRACT change (additive protocol). Fixes node-mode no-op too. |
| **2** | `wt/segment-ui` | `DagView` multi-select, `Playground.tsx` segment selector + pick panel, `gen:api` | segment-dispatch | **Priority 1.** Re-adds `"segment"`; empty-selection guard. |
| 3 | `wt/image-client` | new `images/client.py` `ImageClient` ABC + registry, OpenAI image provider, image pricing, `MockImageClient` | M4 main | **Priority 2.** No DB/UI; pure provider layer. CONTRACT-adjacent (`deep/client.py` pattern). |
| 4 | `wt/image-dispatch` | dispatch image runs, `.aitap/runs/<id>/images/` storage, cost gate, sidecar `image_path` | image-client | **Priority 2.** |
| 5 | `wt/image-ui` | image grid page/component, enlarge, per-column params | image-dispatch | **Priority 2.** |

### Suggested order

1. **wt/segment-dispatch**, then **wt/segment-ui** (priority 1 — land and ship segment mode first).
2. **wt/image-client** → **wt/image-dispatch** → **wt/image-ui** (priority 2 — strictly serial; each needs the prior).

segment-dispatch and image-client are independent and *could* run in parallel, but per the stated priority, segment mode lands and merges before image work starts.

Total: 2 segment worktrees + 3 image worktrees.

---

## Open questions / future work

- **Vision judge (M5+1).** Once the grid exists, a vision-capable judge can score image outputs and unlock the same self-iteration loop M4 built for text. Needs a `judge_type` discriminator on the dimension config (the schema is already forward-compatible).
- **Seed/determinism for images.** Reproducible grids need seed control; not all providers expose it. Defer until the first provider is wired and we know its knobs.
- The two contract-shaping decisions (A·D1 explicit `pipeline_mode`; B·D1 separate `ImageClient`) are **signed off** — `wt/segment-dispatch` and `wt/image-client` can start against them.

---

## References

- `docs/wave-4-design.md` — prior wave; format reference.
- `CONTRACTS.md` — frozen files (`server/routes/__init__.py`, `deep/client.py`) + additive change protocol.
- `WORKTREES.md` — parallel-worktree development pattern.
- `src/aitap/playground/pipeline_runner.py` — the segment runner this wave exposes.
