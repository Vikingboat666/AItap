# Changelog

All notable changes to `aitap` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Seven PRs land between 0.1.0a3 and the eventual 0.1.0a4 cut. The version stays unreleased until the multi-provider redesign (PR #38 + the three follow-up worktrees `wt/profile-client`, `wt/profile-ui`, `wt/profile-cleanup`) is complete — that effort changes the contract in a breaking way and the release notes describe both halves together.

### Added

**Pipeline `segment` mode — full path** (Wave 5 Part A)
- PR #32 (`wt/segment-dispatch`) — `RunCreate` gains additive `pipeline_mode: Literal["node","segment","end_to_end"] | None` + `pipeline_node_id: str | None`. `playground.dispatch` stops hardcoding `"end_to_end"` and honours the requested mode (fixes a latent node-mode no-op too). 422 on empty segment / conflicting selectors / missing node id.
- PR #33 (`wt/segment-ui`) — `DagView` becomes a controlled selection component; Playground gains a node-pick panel + the `segment` mode entry; mode/target switches clear stale selectors; non-contiguous selections show a non-blocking warning.

**Web UI internationalisation** (`react-i18next`)
- PR #34 (`wt/i18n`) — every page and component, en + zh. Language switcher in the top nav, persisted via `localStorage`, follows browser language on first launch. `i18n.parity.test.ts` fails CI when en/zh key sets drift. New non-negotiable section in `CLAUDE.md` documents the bilingual rule.

**Secure API-key management** (`docs/settings-ui-design.md`, now superseded by `docs/profiles-design.md`)
- PR #35 (`wt/settings-ui`) — OS keyring is the primary store (Credential Manager / Keychain / Secret Service via `keyring>=25.7.0`); `~/.aitap/secrets.yaml` is an opt-in fallback gated by an explicit UI confirmation (409 → confirm dialog → retry with `use_fallback=true`). Single-owner `aitap.secrets` module with `get_key` / `key_status` / `set_key` / `delete_key` and AST-enforced import discipline. Global `logging.Filter` strips `sk-…` / `Bearer …` tokens before any handler emits. New API: `GET /api/settings.keys`, `POST/DELETE /api/settings/key`, `POST /api/settings/test/{provider}`. New Settings page; missing-key banner on Inventory + Playground; plain-language `--deep` CLI guard.

**Plain-language UI copy rule**
- Committed alongside PR #35 as a third non-negotiable `CLAUDE.md` section: every user-facing string (UI / CLI / errors / empty states) must read like everyday English/中文, name the next action, and never expose stack traces or raw status codes.

**SPA fallback for React Router subpaths**
- PR #36 (`wt/spa-fallback`) — refreshing on `/settings`, `/playground/...`, `/pipelines/<id>`, `/history/<id>` previously returned FastAPI's `{"detail":"Not Found"}`; a custom `_SpaStaticFiles` subclass now serves `index.html` for those paths while leaving `/api/*` 404s honest.

**Settings page — provider / model / judge_model defaults**
- PR #37 (`wt/settings-defaults`) — adds a Defaults card to the Settings page so the user can pick the default provider, default model, and judge model from the UI; the choice persists to `.aitap/config.yaml` (only the `provider:` block is touched, the `cost:` block stays untouched). Switching provider clears the model + judge inputs so a mismatched combination can't be saved (segment-ui target-switch pattern).

**Multi-provider redesign — backend foundation** (`docs/profiles-design.md`)
- PR #38 (`wt/profile-model`) — first of four serial worktrees. Adds the profile data model (`Profile` / `Defaults` / `ProfileUpsertRequest` / `ProfileTestResponse`), profile-id keyring API in `aitap.secrets` (parallel to the legacy provider-keyed surface), `ProfileConfig` / `DefaultsConfig` in `aitap.config`, YAML round-trip in `aitap.config_io` (preserves the legacy `provider:` block), and the CRUD routes (`GET/POST/PUT/DELETE/test` `/api/profiles`) plus `PUT /api/settings/defaults`. Decisions 1 + 2 (auto-null defaults on delete, slug algorithm) implemented and tested. **Stage-disciplined: no legacy code path is deleted yet** — `wt/profile-client` plugs the LLM-client factory into the new contract, `wt/profile-ui` rebuilds the Settings page around it, `wt/profile-cleanup` removes the legacy surface.

**Multi-provider redesign — LLM client + probe + pricing** (`docs/profiles-design.md`)
- PR #40 (`wt/profile-client`) — second of four serial worktrees. Adds `OpenAICompatClient` (mandatory `base_url` + `api_key`, single code path for DeepSeek / Moonshot / Groq / Together / MiMo / SiliconFlow / Qwen DashScope / Ollama / LM Studio) alongside the legacy `OpenAIClient`; `AnthropicClient` gains an explicit keyword-only `base_url` (default `https://api.anthropic.com`) so a private Anthropic-compatible gateway just becomes a `ProfileConfig` change. `aitap.deep.factory.get_client_for_profile(profile, api_key)` dispatches on `profile.protocol`. `POST /api/profiles/{id}/test` replaces its PR #38 stub with a real probe: resolves the key via `secrets.get_key_for_profile`, builds the per-profile client, sends the documented `ping` shape (`messages=[{role:"user", content:"ping"}]`, `max_tokens=4` — Decision 3), and maps `ProviderAuthError` / `ProviderRateLimitError` / `ProviderError` / unexpected exceptions onto the four reason slots with plain-language details that name the next action; SDK exception strings never reach the response body (B2 regression discipline from PR #35). `deep/pricing.py` gains rows for DeepSeek (`deepseek-chat`, `deepseek-reasoner`), Moonshot (`moonshot-v1-32k`, `moonshot-v1-128k`), Groq (`llama-3.1/3.3-70b-versatile`), and Together (`meta-llama/Llama-3.3-70B-Instruct-Turbo`) under the `openai-compat` provider key. Vendors / models whose published rates couldn't be pinned (Qwen DashScope, SiliconFlow, MiMo, Ollama, LM Studio) are deliberately omitted — the UI renders `cost: unknown` for those rather than the misleading `$0.00`. Import discipline: `server/routes/profiles.py` joins `_ALLOWED_FILES_PROFILE` in `test_secrets_import_discipline.py` (the route layer is the single seam where the raw key leaves `aitap.secrets`; the LLM client classes take the key as a constructor arg and don't touch the vault). Stage discipline preserved: legacy `OpenAIClient` signature and the PR #35 `/api/settings/key*` routes are untouched — `wt/profile-cleanup` retires them.

**Multi-provider redesign — Settings page rebuild + preset templates + Inventory banner** (`docs/profiles-design.md`)
- PR #42 (`wt/profile-ui`) — third of four serial worktrees. Settings page becomes a 3-section experience (642 → ~135 lines): **Defaults card** (default model + judge picker sourced from `/api/profiles`) → **Profiles list** (row per profile with masked key + Test / Edit / Delete / Set-as-default + Set-as-judge from a "..." menu) → **Add profile form** (label / base_url / api_key / model_id / protocol with a preset chip row above and a Manage presets link). The Manage presets dialog (add / edit / delete / reset-to-defaults) edits `.aitap/profile-presets.json`, which seeds 11 starter rows on first launch (Anthropic / OpenAI / DeepSeek / Moonshot Kimi / MiMo Xiaomi / Groq / Together / Qwen DashScope / SiliconFlow / Ollama / LM Studio — exact match to design doc's "Seeded set" table). Inventory's `MissingKeyBanner` switches data sources from the legacy `/api/settings.keys` array to `/api/profiles`, with two plain-language messages: empty pool ("No model profiles yet. Add one in Settings…") and pool-but-no-key ("None of your profiles have an API key. Open Settings to add one."). `pages/Settings.tsx` drops every reference to the legacy `/api/settings/key*` endpoints — the hand-rolled `settings-keys.ts` wrapper still exists but is no longer imported from the page (`wt/profile-cleanup` deletes the file). New `api/profiles.ts` hand-rolls `Profile` / `ProfileUpsertRequest` / `ProfileTestResponse` / `ProfilePreset` types + fetch wrappers because the codegen pass runs in cleanup — types match the backend contract exactly. Security discipline carried from PR #35: API key inputs use `type="password"` + `autoComplete="new-password"`; `setApiKey("")` runs in the `finally` block so a save failure also clears the typed key from React state. a11y: every confirm/edit dialog binds Esc to close and moves initial focus to the safe action; key stability for ManagePresets draft rows now uses `crypto.randomUUID()` instead of array index. en + zh i18n (136/136 keys, 0 drift). New backend e2e canary `tests/integration/test_profiles_e2e.py` (3 tests) plants `sk-fake-profile-CANARY` through the profile flow and asserts it never appears in any HTTP response body, log record, project `.aitap/` file, sqlite column, or `~/.aitap/` entry outside `secrets.yaml`. Per-frontend canary in `Settings.test.tsx` mirrors the same assertion for the rendered DOM. Stage discipline preserved: legacy `Provider` enum / `SettingsResponse.keys` / `/api/settings/key*` routes are untouched in this PR — `wt/profile-cleanup` removes them and bumps the contract to v3.

### Changed

- `aitap.__version__` reads from installed package metadata (`importlib.metadata.version`) so `aitap --version` tracks `pyproject.toml` as the single source of truth.
- `routes/__init__.py` `SettingsResponse` gains `keys: list[ProviderKeyStatus]` (PR #35) and `defaults: Defaults` (PR #38). Both additive — old clients ignoring them still get a well-shaped response.

**Documentation-currency mechanical enforcement**
- PR #39 (`wt/doc-currency`) — adds `tests/unit/test_doc_currency.py` with two test-gate guards: `test_changelog_unreleased_references_every_recent_pr` scans every squash-merge commit since the last released `v…` tag and fails if any `#NNN` is missing from `CHANGELOG.md`'s `[Unreleased]` section (with `[no-changelog]` opt-out for trivial PRs); `test_every_design_doc_carries_an_explicit_status_line` requires each `docs/*-design.md` to declare its status as Draft / Approved / Implemented / Partial / Superseded in the first 30 lines. Expands the existing `PULL_REQUEST_TEMPLATE.md` checklist to mark both items 🤖 enforced, and adds a "Documentation currency — non-negotiable" section to `CLAUDE.md`. Backstops the seven-PR drift this changelog already documented.
- PR #41 (`chore/worktrees-honesty-post-pr40`) — post-merge housekeeping after PR #40: flips `wt/profile-client` in `WORKTREES.md` from ⏳ next to ✅ merged (commit `a8ba11e`, with a one-line summary of what shipped), elevates `wt/profile-ui` to ⏳ next, and bumps `docs/profiles-design.md` Status from `Approved` to `Partial` (2/4 worktrees landed). Doc-only diff — no code or test changes.

### Quality

- 624 backend tests (was 502 at 0.1.0a3) + 56 UI component tests (was 24).
- Pyright strict + ruff clean across Python 3.10/3.11/3.12.
- Every PR went through the established four-gate bar (pyright / ruff / pytest / pnpm typecheck-lint-test-build) and an Opus 4.7 review-to-ACCEPT loop.

### Coming in 0.1.0a4

The remaining three multi-provider worktrees (`wt/profile-client`, `wt/profile-ui`, `wt/profile-cleanup`) will land before the version is cut. The cleanup worktree carries the **BREAKING:** notes — `Provider` enum, `ProviderKeyStatus`, `SetKeyRequest`, `TestKeyResponse`, and the `/api/settings/key*` route family are removed and replaced by `/api/profiles*`. Contract version bumps to 3.

## [0.1.0a3] — 2026-05-23

Wave 4 — M4 self-iteration loop lands. `aitap` can now run a real critique-and-revise loop: an LLM-as-judge scores each round on multiple dimensions, a critic rewrites the prompt (auto / guided / manual), and the loop converges against a baseline-relative target. Driven entirely from the Web Playground's Auto-iterate panel against the `/api/iterate` session endpoints.

### Added

**LLM-as-judge** (Wave 4 wt/judge)
- `aitap.iterate.judge.score_outputs` — grades per-case outputs (read from the `.aitap/runs/<id>/outputs.jsonl` sidecar) along a configurable list of `Dimension`s; one LLM call per case so a single bad case never poisons its siblings. Unparseable judge responses degrade to a zero score rather than crashing the round.
- `judge_defaults` / `judge_models` — a default multi-dimension rubric (user-overridable per Decision 1) plus the pydantic shapes for `Dimension` / `JudgeScore`.
- Provider-agnostic: the only LLM dependency is the `LLMClient` ABC; the suite stays offline via `MockLLMClient`.

**Critique-and-revise** (Wave 4 wt/critic)
- `aitap.iterate.critic.revise` — single entry point over three modes (Decision 2): `auto` (free LLM rewrite), `guided` (rewrite under a user instruction), `manual` (user supplies the full template, no LLM). Returns a `RevisedPrompt` value only — persistence is the loop's transaction boundary, keeping the two-writer race out of the critic. Critic calls use `temperature=0` for deterministic convergence.

**Impact analyzer** (Wave 4 wt/impact, Decision 4)
- `aitap.iterate.impact` — pure, no-LLM, no-DB graph half of the loop. `analyze` BFS-walks the scanner `Pipeline` DAG from the iterated node and returns each downstream consumer with hop distance + traversed edge kinds; `assess_status` classifies each node `verified` / `regressed` / `improved` / `unverified` from before/after weighted scores; `serialize_status_for_iterations` projects to the `{node_id: status}` shape persisted on `iterations.downstream_status`. Downstream re-run is warn-by-default, opt-in.

**Loop orchestrator** (Wave 4 wt/loop)
- `aitap.iterate.iterate_loop` — ties judge + critic + impact + iterations DAO into one `/iterate` session: baseline round → per-round aggregate-feedback → critic revise → atomic (new `prompt_versions` row + dispatch + judge score + iteration row in a single `transaction`) → convergence check. Convergence priority (Decision 3): delta-from-baseline > absolute target > stagnation-window > max-rounds; no absolute default.
- `ConvergenceConfig` — `delta_from_baseline=0.15`, `stagnation_window=3` / `epsilon=0.02`, `max_rounds=5`.

**Iterations persistence** (Wave 4 wt/store, Decision 5)
- New `iterations` table + `aitap.store.iterations` DAO records each round's scores, converged reason, final version, and downstream-impact JSON.

**Per-case output sidecar** (Wave 4 prereq wt/runs)
- The playground dispatch now persists per-case run outputs to `.aitap/runs/<id>/outputs.jsonl` (deferred from 0.1.0a2) — the judge reads this sidecar.

**Iterate API** (Wave 4 wt/api-iterate)
- `POST /api/iterate` (202 + pre-minted `session_id`), `GET /api/iterations/{session_id}`, `GET /api/iterations/{session_id}/latest`, `GET /api/iterations/by-prompt/{prompt_id}`. The loop runs as a FastAPI `BackgroundTask`; a failed run updates its placeholder row to a `failed` sentinel in place.

**Web UI** (Wave 4 wt/ui-iterate)
- `AutoIterateModal` — pick prompt + dataset, choose revise mode, optionally expand a convergence-config form, start a background session. Dataset is an explicit, required text field (matching `.aitap/datasets/<name>.cases.jsonl`).
- `IterationProgress` — polls session status, renders per-round multi-dimension score bars + converged reason + final version; polling stops on terminal status.
- `IterationTimeline` — history of iteration sessions per prompt.
- `DownstreamImpactBanner` — surfaces "N unverified consumers" when the iterated prompt sits upstream in a pipeline.

### Changed

- `aitap.iterate` re-exports the full `iterate_loop` / `ConvergenceConfig` from `loop.py` while keeping the Wave 3 `iterate_one_round` stub for the existing `POST /api/runs/{id}/iterate` single-round fallback.

### Quality

- 502 backend tests (was 356 in 0.1.0a2) + 24 UI component tests (was 6).
- Pyright strict + ruff clean across Python 3.10/3.11/3.12.
- Every Wave 4 worktree merged via squash PR after the four-gate bar (pyright/ruff + backend tests + frontend lint/test/build + Opus 4.7 review to ACCEPT).

### Known limits / not yet shipped

- Self-iteration is Playground-driven only; no `aitap iterate` CLI command yet.
- Image-prompt grid view — 0.1.0a4 (M5).
- Pipeline `segment` mode UI control — M5.
- TypeScript scan support, runtime data capture — v0.2.

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

[Unreleased]: https://github.com/Vikingboat666/AItap/compare/v0.1.0a3...HEAD
[0.1.0a3]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a3
[0.1.0a2]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a2
[0.1.0a1]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a1
