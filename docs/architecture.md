# Architecture

aitap is a CLI + local web app. Everything happens on your machine; the
only network calls are the LLM calls your own project would already make
(deep-scan and iteration use your own API keys).

## High-level flow

```
[your code]
    │  scan
    ▼
[Inventory: standalone Prompts + Pipelines (DAG of LLM calls)]
    │
    ▼
[Web Playground: pick prompt/pipeline → attach dataset → batch run]
    │
    ├──► [Self-iterate: feedback-driven critique-and-revise loop]
    ├──► [Compare: version / model / param diffs]
    └──► [.aitap/ persistence: history, rollback, git-tracked artifacts]
```

## Layers

### 1. Scanner

Two tiers:

- **L1 — rule-based** (always free, deterministic). Lives in
  `src/aitap/scanner/`. Uses Python's built-in `ast` for semantic
  precision and `tree-sitter-python` as a tolerant fallback for files
  the AST can't parse. Matches known SDK call signatures (OpenAI,
  Anthropic, …) and extracts prompt strings, models, parameters, and
  message roles.
- **L2 — LLM-assisted** (opt-in). Lives in `src/aitap/deep/`. Uses your
  own provider key to recognize custom wrappers, resolve cross-file
  prompt assembly, and infer purpose. Always shows a cost estimate
  before running.

Pipeline detection is rule-based in v0.1: variable data-flow within a
file, the LangChain `|` operator, LlamaIndex query engines, and
intra-file function chaining.

### 2. Storage

`.aitap/` is created by `aitap init` in each consumer project.

| Artifact | Location | Tracked in git? |
|---|---|---|
| Provider config + cost caps | `.aitap/config.yaml` | ✅ |
| Run history, scores, feedback | `.aitap/db.sqlite` | ❌ (gitignored) |
| Extracted prompts | `.aitap/prompts/*.prompt.yaml` | ✅ |
| Extracted pipelines | `.aitap/pipelines/*.pipeline.yaml` | ✅ |
| Test datasets | `.aitap/datasets/*.cases.jsonl` | ✅ |
| Per-run snapshots | `.aitap/runs/<ts>-<prompt>-v<n>/` | ❌ |

Every run is tagged with the current git commit SHA when the consumer
project is a git repo, so results are reproducible.

### 3. Web Playground

FastAPI backend serving a Vite + React frontend. The frontend is built
once and bundled into the wheel — end users never need Node installed.

Three pipeline run modes:

- **Node** — run a single node in isolation.
- **Segment** — run a contiguous subgraph (e.g. `outline → polish`).
- **End-to-end** — feed the source nodes, capture every intermediate
  output to disk, compare against expected sink outputs.

### 4. Iteration loop

Driven by combined feedback: 👍/👎, ideal-answer references, rule
predicates, and an LLM-as-judge. The loop is:

```
critique → revise → re-run → score → converge or pause
```

When iterating a node inside a pipeline, the **impact analyzer** walks
the DAG to find downstream consumers and (optionally) re-runs them as a
regression check, so a local optimization can't silently break the
end-to-end flow.

## Source layout

```
src/aitap/
├── cli.py             # Typer entrypoint
├── config.py          # pydantic-settings
├── scanner/           # L1 rule-based scanner
│   ├── engine.py
│   ├── languages/python.py
│   ├── rules/
│   ├── dataflow/      # pipeline detection
│   └── models.py      # PromptSite, Pipeline, ScanResult (CONTRACT)
├── deep/              # L2 LLM-assisted
│   └── client.py      # LLMClient ABC (CONTRACT)
├── dataset/           # test-case generation
├── playground/        # runner, pipeline_runner, image grid
├── iterate/           # judge, critic, impact, loop
├── store/             # .aitap/ persistence (db.py is a CONTRACT)
├── audit/             # remote-repo audit mode
├── server/            # FastAPI app + bundled static
└── ui/                # Vite + React source
```

The four files marked **CONTRACT** are shared interfaces with downstream
consumers. Changes to them go through the [contract change
protocol](https://github.com/aitap/aitap/blob/main/CONTRACTS.md).

## Design principles

1. **Zero-config first run** — `aitap scan` in any project must produce
   value with no setup.
2. **Progressive enhancement** — L1 is always free; L2 is opt-in,
   transparently priced, and reuses the consumer project's keys.
3. **Local-first** — no cloud, no telemetry, no accounts.
4. **Git-native** — prompts and datasets are diffable text files; run
   results stay local.
5. **Reproducible** — every run pins the consumer commit SHA, provider,
   model, and parameters.
6. **Provider-agnostic** — auto-detect from existing `.env` / config;
   support multiple providers in one project.
