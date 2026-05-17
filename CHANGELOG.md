# Changelog

All notable changes to `aitap` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a2] — 2026-05-17

Wave 3 — M3 Web Playground lands. CLI now opens a real React app instead of a "coming soon" stub; every page (Inventory / PromptDetail / PipelineDetail / Playground / History) hits the live FastAPI surface.

### Added

**Server / FastAPI** (Wave 3 wt/api-prompts, wt/api-runs, wt/runner)
- `aitap.server.app` — `create_app()` + `serve()` (uvicorn launcher). Lazy router discovery via `find_spec` so partial worktree installs still produce a working `/api/health`.
- Read-side: `GET /api/prompts`, `GET /api/prompts/{id}`, `POST /api/prompts/{id}/versions`, `GET /api/pipelines`, `GET /api/pipelines/{id}`, `GET /api/history/{prompt_id}`, `POST /api/history/{prompt_id}/rollback`. Activates the CLI stubs `aitap diff` / `aitap rollback`.
- Write-side: `POST/GET /api/runs`, `POST /api/runs/{id}/feedback`, `POST /api/runs/{id}/iterate`, `GET/PUT /api/settings`, `GET /api/settings/cost-estimate`.

**Playground runner** (Wave 3 wt/runner)
- `playground.runner.run_prompt` — fan-out via `asyncio.gather`, per-case error isolation, cost + token-usage rollup.
- `playground.pipeline_runner.run_pipeline` — `node` / `end_to_end` modes with Kahn topo-sort + cycle detection; per-case intermediates.
- `playground.dispatch.invoke_run` — high-level adapter the API layer calls; builds the LLMClient from settings, loads target + dataset cases, dispatches, persists status + cost.

**Test-case generators** (Wave 3 wt/dataset)
- `aitap.dataset.seed` — read/write cases via `store.files.append_cases`.
- `aitap.dataset.llm_expander` — async LLM-driven variant generation (boundary / adversarial / noise) over the `LLMClient` abstraction.
- `aitap.dataset.code_context` — `infer_input_shape` via stdlib `ast`.
- `aitap.dataset.fixture_miner` — heuristic mining of `tests/` / `fixtures/` / `examples/` for candidate seeds.
- `aitap.dataset.generate_cases` — orchestrator over the four modes.

**Web UI** (Wave 3 wt/ui-inventory, wt/ui-playground)
- React 18 + Vite + Tailwind + react-router + tanstack/react-query.
- Inventory (dual-tab Prompts/Pipelines) / PromptDetail (version list + diff placeholder) / PipelineDetail (DagView with EdgeKind styling) / Playground (prompt selector → CaseEditor → Run → ResultsTable) / History (version timeline + SVG bar chart of avg_score + diff modal placeholder).
- Loading skeletons + error retry on every fetch.
- React Query optimistic updates for feedback (👍/👎 feels instant).
- `aitap ui` opens the browser by default; `--no-browser` to suppress.

**Testing**
- vitest + MSW + @testing-library/react harness; 6 component tests covering loading/success/error for Inventory/PromptDetail/PipelineDetail.
- Dispatch integration: `POST /api/runs` end-to-end test asserts status truly transitions `running → done`.

**Iteration scaffolding**
- `aitap.iterate.iterate_one_round` — single-round stub that records a new `prompt_versions` row from accumulated feedback. Real critique loop lands in 0.1.0a3 (M4).

### Changed

- `store.db.connect()` sets `check_same_thread=False` so connections can cross FastAPI's threadpool boundary safely. Callers must still not share a single connection across threads.
- `store.db.transaction(immediate=False)` gains an `immediate` flag that issues `BEGIN IMMEDIATE`; used by the iterate path to race-proof the version bump under concurrent calls.
- `package.json:gen:api` fixed — invoked a non-existent binary on main.

### Quality

- 356 backend tests (was 248 in 0.1.0a1) + 6 UI component tests.
- Pyright strict + ruff clean across Python 3.10/3.11/3.12.
- Pre-commit `no-hardcoded-local-paths` hook + `CLAUDE.md` guardrails added when the repo went public.

### Known limits / not yet shipped

- Per-case run outputs persistence (JSONL sidecar at `.aitap/runs/<id>/outputs.jsonl`) — deferred to M4.
- Pipeline `segment` mode hidden from the UI mode selector until M5 ships the node-pick control (the runner still supports it).
- Self-iteration loop (LLM-as-judge + critique-and-revise) — 0.1.0a3 (M4).
- Image-prompt grid view — 0.1.0a4 (M5).
- TypeScript scan support, runtime data capture — v0.2.

## [0.1.0a1] — 2026-05-14

First pre-alpha. Wave 1 + Wave 2 features. CLI-only — Web Playground (M3) lands in 0.1.0a2.

### Added

**Scanner (L1, rule-based)**
- Walks a Python project with tree-sitter + Python's `ast`; finds LLM call sites for `openai`, `anthropic`, `langchain`, `llamaindex`, `dashscope` SDKs.
- Extracts prompt templates (string literals, f-strings, jinja2 basics, multi-line concat); identifies model/temperature/max_tokens/response_format params.
- Inspects `.env` and config files to identify which providers are configured (key existence only — never reads key values).

**Pipeline detection** (Wave 2 wt/dataflow)
- Four detectors: variable data-flow tracking, LangChain `|` operator chains, LlamaIndex query-engine patterns, same-file function-wrapper composition.
- Builds Pipeline DAGs from detected edges; assigns content-hashed Pipeline IDs so re-scans are idempotent.

**Persistence** (Wave 2 wt/store)
- `aitap init` creates `.aitap/{prompts,pipelines,datasets,runs}/` + `config.yaml` + `.gitignore` block.
- After every scan, results land in SQLite (`db.sqlite`) + git-friendly YAML mirrors (`prompts/*.prompt.yaml`, `pipelines/*.pipeline.yaml`).
- Same-named prompts disambiguated by content-hash filename suffix.
- Git context (commit SHA, dirty/clean) recorded on every scan when in a git repo.

**Audit mode** (Wave 2 wt/audit)
- `aitap audit gh:owner/repo` clones a remote repo, runs L1 scan, prints report, cleans up.
- L2 hard-gated off — never spends API key on third-party code.

**Provider clients** (Wave 2 wt/providers)
- `AnthropicClient` (Messages API) and `OpenAIClient` (Chat Completions); both lazy-import their SDKs so `aitap` installs cleanly without optional extras.
- Centralised pricing table for cost estimation; provider errors wrapped in `ProviderAuthError` / `ProviderRateLimitError` / `ProviderError`.

**L2 deep scanner** (Wave 2 wt/deep-scan)
- `aitap scan --deep` runs three LLM-assisted enrichers concurrently: wrapper confirmation, cross-file template resolution, prompt purpose inference.
- Cost-confirmation gate before any API call; `--yes` skips for CI/scripted use.
- Auth/runtime errors during L2 surface as warnings + L1 fallback rather than crashes.

**CLI** (Wave 1 wt/cli-scaffold)
- `aitap init` / `aitap scan` (with `--rules-only`, `--deep`, `--json`, `--yes`) / `aitap audit`.
- `aitap ui` / `aitap diff` / `aitap rollback` registered with full flag surface; bodies land in 0.1.0a2.
- Stdout/stderr forced to UTF-8 at startup so Windows GBK terminals don't crash on rich-rendered Markdown.

### Quality

- 248 unit tests + 7 integration tests, ~85% coverage.
- ruff + pyright (strict) clean across Python 3.10/3.11/3.12.
- CI matrix: ubuntu/macos/windows × py3.10/3.11/3.12.
- mkdocs-material docs site (build --strict clean).

### Known limits / not yet shipped

- Web Playground (Inventory / Playground / History pages with real API) — landing in 0.1.0a2 (M3).
- Self-iteration loop (LLM-as-judge + critique-and-revise) — landing in 0.1.0a3 (M4).
- Image-prompt grid view — landing in 0.1.0a4 (M5).
- TypeScript scan support, runtime data capture decorator — v0.2.

## [Wave 0] — initial skeleton

- pyproject.toml, directory layout, ruff/pyright/pre-commit configs, contracts doc.

[Unreleased]: https://github.com/Vikingboat666/AItap/compare/v0.1.0a2...HEAD
[0.1.0a2]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a2
[0.1.0a1]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a1
