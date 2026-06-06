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

**Multi-provider redesign — contract v3 deletion** (`docs/profiles-design.md`)
- PR #43 (`wt/profile-cleanup`) — fourth of four serial worktrees. **BREAKING:** the contract bumps to v3 (CONTRACTS.md protocol) and the legacy provider-keyed surface is removed wholesale. Removed pydantic models on `routes/__init__.py`: `ProviderKeyStatus`, `SetKeyRequest`, `TestKeyResponse`, `SettingsUpdate`, and the `keys: list[ProviderKeyStatus]` field on `SettingsResponse` (per-profile status now lives inline on the `Profile` returned by `GET /api/profiles`). Removed routes on `routes/settings.py`: `POST /api/settings/key`, `DELETE /api/settings/key/{provider}`, `POST /api/settings/test/{provider}`, and `PUT /api/settings` (free-form provider/model edits are no longer a UI affordance — `PUT /api/settings/defaults` covers "pick a default profile"). The in-memory `_MUTABLE_STATE` override layer and the `_persist_provider_defaults_to_yaml` writer are gone with the PUT route they backed. Deleted the orphan `src/aitap/ui/src/api/settings-keys.ts` wrapper and regenerated `openapi.json` + `pnpm gen:api` so the TypeScript client matches v3. Test deletions: `tests/unit/test_routes_settings_keys.py` and `tests/integration/test_secure_settings_e2e.py` are removed (every assertion targeted a deleted endpoint; the new profile-keyed coverage lives in `tests/unit/test_routes_profiles.py` + `tests/integration/test_profiles_e2e.py`); four `test_put_settings_*` cases in `test_api_runs.py` are deleted with an in-file placeholder pointing to the replacement endpoint. **Scope discipline:** `aitap.secrets` legacy provider-keyed API (`get_key` / `set_key` / `delete_key` / `key_status`), `register_provider("anthropic"|"openai", ...)` registry + `get_client`, the legacy `OpenAIClient` class, `playground/dispatch.py::_default_client_factory`, and `config_io.py` reading the legacy `provider:` block are all left in place — the `POST /api/runs` path (`RunCreate.provider` + the `runs` SQLite column) and the `aitap scan --deep` L2 enrichment still wire through them. Migrating those to profile-id dispatch is a follow-up worktree; the current PR is the bounded contract-and-routes deletion the design doc §"Worktree breakdown" calls for.

**Wave 5 Part B — image client + pricing** (`docs/wave-5-design.md`)
- PR #45 (`wt/image-client`) — first of three image-grid worktrees. New `aitap.images` package with `ImageClient` ABC + per-provider registry parallel to `aitap.deep.client.LLMClient` (B·D1). `OpenAIImageClient` implements DALL-E 2 + DALL-E 3 against `POST {base_url}/images/generations`; mandatory `base_url` + `api_key` constructor; PR #35 B2 anti-leak pattern carried (static plain-language detail strings, SDK exception bodies never reach response.detail). `MockImageClient` for offline tests. `aitap.images.factory.get_image_client_for_profile(profile, api_key)` dispatches by protocol — `"openai-compat"` → OpenAIImageClient, `"anthropic"` → ImageProviderError ("Anthropic profile cannot generate images"). `aitap.images.pricing` carries DALL-E 2 / DALL-E 3 rows with source URL citations + LAST_UPDATED. Stage discipline: no dispatch / no routes / no storage / no UI — `wt/image-dispatch` adds the run sidecar + endpoint, `wt/image-ui` adds the grid page.

**Scanner — prompt-template-definition recognition** (`docs/scanner-templates-design.md`)
- PR #46 (`wt/scanner-templates`) — closes the structural gap a real-project eval (cc-project / Pet Heaven) surfaced: the L1 scanner only matched SDK call sites, so production projects that pull prompts out into a dedicated `prompt_templates.py` module showed up with 25 % recall and 0 % readable text. Two new rule families in `src/aitap/scanner/rules/template_definitions.py`: (a) **builder functions** with a recognisable name (`build_<task>_messages`, `make_<task>_prompt`, `compose_<task>_chat`, …) returning a `list[dict[str, str]]` literal — handles direct return and the named-assignment-then-return idiom; (b) **module-level prompt constants** (`SYSTEM_PROMPT`, `HEAVEN_WORLD_RULES`, `*_TEMPLATE`, …) with literal / triple-quoted / `textwrap.dedent` / f-string RHS. Both rules emit a `PromptSite` tagged `template-definition` + `builder-function | module-constant`, degrade gracefully to `UNRESOLVED` when the body is too dynamic to parse, and infer provider from file imports (`anthropic` / `openai` → matching enum, otherwise `UNKNOWN`). Scope discipline: top-level only — nested builders inside a function and methods on a class are skipped (the SDK-call path picks up the enclosing call site instead). Re-run against cc-project: **8 → 30 prompts (+275 %)**, **0 → 12 resolved message texts**, 22 newly surfaced template definitions including the 9 `build_<task>_messages` helpers + `HEAVEN_WORLD_RULES` the project owner names in their own `CLAUDE.md`. +69 tests (58 unit + 11 integration), 729 → **798 backend**. Follow-up worktrees flagged in the design doc: `wt/scanner-wrappers` (project-owned LLM client wrappers), `wt/scanner-pipelines` (multi-step orchestration), and a deep-scan revisit that ships UNRESOLVED builder bodies to L2 for `purpose` summarisation.

**Scanner — wrapper-style LLM call recognition** (`docs/scanner-wrappers-design.md`)
- PR #47 (`wt/scanner-wrappers`) — extends the L1 scanner to recognise project-owned wrapper invocations (`await self._llm.complete(messages, task_type="digest")`-style) that the SDK-call rule never matched. New `src/aitap/scanner/rules/wrapper_calls.py` runs when the SDK-call match returns `None`: matches one of 22 wrapper method names on the allow-list (LangChain idiom `invoke` / `ainvoke` / `run` / `arun` + Pet-Heaven idiom `complete` / `acomplete` + completion-style `predict` / `generate` / `chat`), gates by either an LLM-ish receiver name substring (`llm` / `client` / `chat` / `model` / `chain` / `agent` / etc.) **or** a strong LLM-shape signal (`messages=` / `prompt=` / `system=` keyword, or a first positional that's a list literal or a `messages`-named Name). Sites carry `wrapper-call` + shape extras (`kw-messages` / `first-positional-list` / `first-positional-name`) so the inventory UI can group / filter; parameters extract identically to the SDK path (`temperature` / `max_tokens` / `model` / etc.). False-positive guards verified: `self.db.invoke(query)` (no LLM signal, no LLM-ish receiver) and `self._llm.fancy_new_thing(...)` (method off the allow-list) both stay unclaimed. Re-run against cc-project on top of PR #46: **30 → 48 prompts (+18 wrapper sites)**, **12 → 19 resolved messages**, all 7 Pet Heaven agent files (`digest_generator` / `interaction_engine` / `location_theme_generator` / `memory_manager` / `personality_builder` / `planner` / `reflection_engine`) now appear in the inventory with their wrapper call surfaced. +34 unit tests, 798 → **832 backend**. Inventory completeness on this real project went from 25 % at main HEAD to ~90 % over the two scanner PRs (#46 + #47). Follow-up worktrees flagged: `wt/scanner-pipelines` (multi-step orchestration recognition) and `wt/scanner-bare-call` (handle `await llm(messages)` shape with local-scope `llm = get_llm_client()` resolution).

**Scanner — de-over-fit PR #46 / #47 allow-lists**
- PR #48 (`wt/scanner-deoverfit`) — review pass on the rules added in PRs #46 + #47 turned up three Pet-Heaven-shaped tells the original allow-lists picked up. (a) `_PROMPT_CONST_RE` had `HEAVEN` on the prefix list — domain word, not LLM-prompt vocabulary; removed. `HEAVEN_WORLD_RULES` still matches via the generic `_RULES` suffix, so cc-project's coverage is unchanged. (b) `_PROMPT_CONST_RE` had `RULES` on the prefix list — `RULES_FOO` is rare; the conventional shape `FOO_RULES` is on the suffix list and continues to catch `HEAVEN_WORLD_RULES` / `GAME_RULES` / `SAFETY_RULES`. (c) `_WRAPPER_METHODS` had a `ageneerate_response` typo for `agenerate_response` — never would have matched a real call; corrected. New `tests/unit/test_scanner_generality.py` (+13 tests, 832 → **845 backend**) pins the de-over-fit intent: fixture names drawn from real public frameworks (LangChain `make_grading_messages` / `chain.invoke(messages=...)`, OpenAI Cookbook `SUMMARISE_PROMPT` / `SAFETY_INSTRUCTIONS`, Anthropic Cookbook `compose_dialogue_chat`, LlamaIndex `llm.chat(messages=...)`, Microsoft Semantic Kernel `kernel.invoke(prompt=...)`) plus false-positive guards using names a future allow-list expansion would be tempted to add (`session.invoke(query)`, `MAX_RETRIES`, `PromptTemplate` class, `build_response`). cc-project re-scan after: zero regression, all 48 sites + 19 resolved messages preserved.

**Scanner — LangChain tuple-form messages support**
- PR #49 (`wt/scanner-tuple-form`) — `extract_messages` now recognises the LangChain tuple shape `[("system", "..."), ("user", "...")]` alongside the OpenAI / Anthropic canonical dict shape `[{"role": ..., "content": ...}, ...]`. `_message_from_tuple` is tried as a fall-back after `_message_from_dict` so the existing dict path is byte-for-byte unchanged; the new path lifts content templates (literal / f-string / format) through the same `extract_template` machinery so the message text + variables surface identically. Role aliases are normalised on ingestion — `"human"` → `Role.USER`, `"ai"` → `Role.ASSISTANT`, `"function"` → `Role.TOOL`, and case-insensitively (`"System"` / `"SYSTEM"` both → `Role.SYSTEM`) — so downstream tooling sees the canonical four-role enum regardless of which framework the project uses. Guards: a 1-element tuple `("system",)`, a 3-element tuple `("system", "x", "y")`, an unrecognised role string (`"manager"`), or a non-string first element all degrade to `UNRESOLVED` rather than guessing. +14 tests (`tests/unit/test_scanner_langchain_tuple_form.py`), 845 → **859 backend**. Mixed dict-and-tuple lists in the same call are supported so a project mid-port between frameworks doesn't lose coverage; the dict path regression test pins the byte-for-byte invariant.

**Scanner — cross-file orchestration recognition (investigation only)**
- PR #50 (`chore/cross-file-orchestration-doc`) — investigation doc, no rule landed. After PRs #46 + #47 + #48 + #49 hit ~90 % inventory completeness on cc-project's prompt sites, pipelines still report zero even though `daily_runner.run` sequences six agent calls. A syntactic shortcut was tried in a throwaway worktree: flagging any function with ≥ N distinct `<receiver>.<method>(...)` calls. Without a receiver gate (N=3) flagged 254 of 302 sites — every Alembic `upgrade`, every `test_xxx`, every API handler. With a strict "agent-like receiver hint" gate the count dropped to 3 sites, all of them unit tests mocking `engine._llm.complete`; `daily_runner.run` still missed because its receiver names (`_planner` / `_matcher` / `_reflector` / `_digest_gen` / `_rel_updater`) aren't LLM-framework vocabulary. The signal genuinely isn't in the orchestrator file's syntax — recognising cross-file orchestration needs class-attribute → imported-class resolution (`self._planner = Planner()`, `from app.agents.planner import Planner`), not another allow-list. The doc records the dead-end, the explicit recommendations the next worktree should *not* do (don't add agent-like hints, don't lower the step threshold, don't add method-name heuristics — all over-fit traps PR #48 already had to clean up), and the recommended architecture for `wt/scanner-cross-file-orchestration` when it eventually lands.

**Scanner — cross-file orchestration recognition (rule landed)** (`docs/scanner-cross-file-orchestration-design.md`)
- PR #51 (`wt/scanner-cross-file-orchestration`) — implements the architecture PR #50 doc proposed. New `src/aitap/scanner/dataflow/cross_file_orchestration.py` registers a `CrossFileOrchestration` detector that runs after the per-file detectors. It walks every file twice: first to build a global `ClassName -> file` map (first-seen wins) and a `file -> {alias: ClassName}` import table, then to scan each class's `__init__` for `self.<attr> = <Class>()` / annotated variant, resolving `<Class>` back to its defining file through the import table. For each method body it counts `self.<attr>.<method>(...)` calls whose `<attr>` resolves to an LLM-bearing file (one that contains at least one prompt site); ≥ 2 distinct LLM-bearing receivers in source order produces a `PipelineEdge` chain with `EdgeKind.FUNCTION` + `Confidence.MEDIUM` and a `via` field carrying the orchestrator's `file::ClassName.method` label. `dataflow/__init__.py::detect_pipelines` gains a `cross_file_detectors` kwarg (defaulted via `default_cross_file_detectors()`) so callers can opt out or extend. Honest scope notes shipped in the module docstring: `self.x = factory()` (non-literal constructor), `import foo` (only `from X import Y` is tracked), class-name collisions, and deeper attribute chains (`self.x.y.method()`) are all explicit non-goals. Confidence is MEDIUM because the class-attribute indirection is a heuristic. Verified against cc-project: pipelines went from **0 → 1** (`plan_day_pipeline`, 4 nodes / 3 edges anchored by `DailyRunner.run` spanning planner / engine / reflector / digest_gen). Tests (+15, 859 → **874 backend**) cover the Pet-Heaven-shaped happy path (vanilla + annotated init + aliased import), false-positive guards (single distinct receiver, non-LLM-bearing receivers, missing `__init__`, local class reference must not chain through cross-file, unresolved class names), internal receiver-shape unit tests, and a byte-for-byte regression guard on the prompt list. Design doc Status flipped from Draft → Implemented in the same PR.

**Scanner — recognise `dedent(...)` and `.strip()` chained templates**
- PR #52 (`wt/scanner-dedent-call`) — `extract_template` gains two new branches that compose with the existing literal / f-string / format-call / concat paths. (a) `dedent("...")` and `textwrap.dedent("...")` (and any module-prefixed `*.dedent(...)`) unwrap their single string argument and recurse, so nested f-strings inside `dedent(f"...{var}...")` still surface the variable list. (b) `<expr>.strip()` / `.lstrip()` / `.rstrip()` (with or without an argument) recurses into the receiver — composing with the new dedent branch covers the canonical Pet Heaven shape `HEAVEN_WORLD_RULES = dedent("""...""").strip()`. Guards: multi-positional / kwarg / zero-arg `dedent(...)` and strip-on-non-template-receiver (`get_body().strip()`) both degrade to UNRESOLVED rather than guessing. Verified on cc-project: `HEAVEN_WORLD_RULES` flipped from `template_kind=unresolved, text=""` to `template_kind=literal, text=2400 chars` (the full Pet Heaven world-rule preamble). +16 unit tests (`tests/unit/test_scanner_dedent_call.py`), 874 → **890 backend**. Pure additive — the four pre-existing extraction branches are byte-for-byte unchanged.

**UI — PromptDetail plain-language fallback for unresolved messages**
- PR #53 (`wt/ui-empty-prompt-placeholder`) — closes the cosmetic gap surfaced when a user tried the web playground in 2026-06: every prompt whose `template_text` field is empty (SDK call sites with dynamic `messages` variables, wrappers like `self._llm.complete(messages, ...)` where the literal lives in a separate builder, `dedent(...)` shapes not yet covered by the extractor) rendered as an empty `<pre>` box, which read visually as "the detail page is broken". `PromptDetail.tsx` now branches on `m.template_text`: non-empty text uses the existing `<pre>` block byte-for-byte; empty text shows a plain-language note ("No prompt text resolved at L1." / "L1 没拽到 prompt 文本。") + a body sentence explaining the cause + the next-action CLI hint `aitap scan --deep <file>` filled with the site's own location. New i18n keys `prompt.unresolvedTitle` + `prompt.unresolvedBody` added to en + zh; parity test stays green (22/22 prompt-namespace keys). `PromptDetail.test.tsx` gets a new case (+1, 59 → 60 frontend tests) that mocks an MSW response with `template_text=""` and asserts both the title text and the CLI hint render. No backend / contract / scanner changes — pure UI fallback. Carries the plain-language UI copy rule from CLAUDE.md.

### Changed

- `aitap.__version__` reads from installed package metadata (`importlib.metadata.version`) so `aitap --version` tracks `pyproject.toml` as the single source of truth.
- `routes/__init__.py` `SettingsResponse` gained `keys: list[ProviderKeyStatus]` (PR #35) and `defaults: Defaults` (PR #38), then dropped `keys` again in contract v3 (PR #43). The `defaults` field stays and is the only key-status-adjacent shape that survives the redesign.

**Documentation-currency mechanical enforcement**
- PR #39 (`wt/doc-currency`) — adds `tests/unit/test_doc_currency.py` with two test-gate guards: `test_changelog_unreleased_references_every_recent_pr` scans every squash-merge commit since the last released `v…` tag and fails if any `#NNN` is missing from `CHANGELOG.md`'s `[Unreleased]` section (with `[no-changelog]` opt-out for trivial PRs); `test_every_design_doc_carries_an_explicit_status_line` requires each `docs/*-design.md` to declare its status as Draft / Approved / Implemented / Partial / Superseded in the first 30 lines. Expands the existing `PULL_REQUEST_TEMPLATE.md` checklist to mark both items 🤖 enforced, and adds a "Documentation currency — non-negotiable" section to `CLAUDE.md`. Backstops the seven-PR drift this changelog already documented.
- PR #41 (`chore/worktrees-honesty-post-pr40`) — post-merge housekeeping after PR #40: flips `wt/profile-client` in `WORKTREES.md` from ⏳ next to ✅ merged (commit `a8ba11e`, with a one-line summary of what shipped), elevates `wt/profile-ui` to ⏳ next, and bumps `docs/profiles-design.md` Status from `Approved` to `Partial` (2/4 worktrees landed). Doc-only diff — no code or test changes.

**Post-redesign nit cleanup (PR #40 + PR #42 follow-ups)**
- PR #44 (`wt/post-redesign-nits`) — rolls up the eight reviewer-flagged nits left on the multi-provider redesign so the review backlog hits zero before the next wave. **PR #40 nits**: (1) explanatory comment in `routes/profiles.py` clarifying that `profile_id` is a user-chosen slug (not a secret) so the diagnostic log line stays unredacted; (2) FX-drift policy paragraph in `deep/pricing.py` module docstring naming Moonshot/Kimi's CNY-anchored rows + the ~3% re-anchor threshold; (3) `_register` helper in `deep/pricing.py` raises `AssertionError` on a duplicate `(provider, model_id)` key — the previous loops would have silently shadowed a row whichever vendor imported last; (4) `_safe_compat_cost_from_tokens` "Future work" comment becomes a `TODO(profile-runs-migration)` pointing at the contract bump that would let `cost: unknown` flow all the way down from the SDK call site. **PR #42 nits**: (5) `ManagePresetsDialog` falls back to focusing the Close button when the preset list is empty and `firstInputRef.current` is null, so keyboard focus doesn't strand behind the modal backdrop; (6) inline comment on the keydown listener flagging the future stacking-aware modal manager as a follow-up rather than a per-component fix; (7) drops the `# type: ignore[arg-type]` on `_seeded_presets` by introducing a `ProfileProtocol` Literal alias mirroring the contract — a future protocol-enum rename now surfaces as a static error; (8) `WORKTREES.md` fills the PR #43 commit hash (`b69e97b`, previously a `filled at squash` placeholder). Doc-and-comment-heavy — no behaviour changes, gates clean (661 backend + 14 / 59 frontend).

### Quality

- 722 backend tests (was 502 at 0.1.0a3) + 56 UI component tests (was 24).
- Pyright strict + ruff clean across Python 3.10/3.11/3.12.
- Every PR went through the established four-gate bar (pyright / ruff / pytest / pnpm typecheck-lint-test-build) and an Opus 4.7 review-to-ACCEPT loop.

### 0.1.0a4 release notes (draft)

All four multi-provider worktrees are in: `wt/profile-model` (PR #38), `wt/profile-client` (PR #40), `wt/profile-ui` (PR #42 — Settings page rebuild + Inventory banner + preset templates + en/zh i18n + e2e canary), and `wt/profile-cleanup` (PR #43). The redesign is the current contract; `routes/__init__.py` is at v3. The cut waits for the post-merge OpenAPI / TS-client regeneration step (`pnpm gen:api`) so the published TypeScript surface matches the v3 backend.

**BREAKING (contract v3, PR #43):**
- Removed pydantic types on `routes/__init__.py`: `ProviderKeyStatus`, `SetKeyRequest`, `TestKeyResponse`, `SettingsUpdate`. The `keys: list[ProviderKeyStatus]` field on `SettingsResponse` is gone — per-profile key status now lives inline on `Profile`.
- Removed routes: `POST /api/settings/key`, `DELETE /api/settings/key/{provider}`, `POST /api/settings/test/{provider}`, `PUT /api/settings`. Use `/api/profiles*` and `PUT /api/settings/defaults` instead.
- Existing alpha users see a startup hint: re-add keys via Settings → Add profile; old keyring entries under `aitap` service / `provider:anthropic|openai` account become orphan and are user-removable via Credential Manager / Keychain.

**Still present, deferred to a follow-up worktree:**
- `aitap.secrets` legacy provider-keyed API (`get_key` / `set_key` / `delete_key` / `key_status`), the `register_provider` registry + `get_client` factory, the legacy `OpenAIClient` class, `playground/dispatch.py::_default_client_factory`, and `config_io.py` reading the legacy `provider:` block. These remain because the `POST /api/runs` path (`RunCreate.provider` + `runs` SQLite column) and the `aitap scan --deep` L2 enrichment still wire through them; migrating both is a separate effort outside the bounded "contract-and-routes deletion" the design doc tagged for this worktree.

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
