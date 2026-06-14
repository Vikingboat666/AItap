# Parallel worktree workflow

This document indexes the **active** worktree work and the standing conventions for spinning up new ones. Historical wave breakdowns (Wave 1 through Wave 4, plus Wave 5 Part A) are preserved in the corresponding `docs/wave-*-design.md` files — those waves are shipped and their worktrees are gone, so we don't list each merged branch here.

For the canonical project state (current release, what shipped when, who owns what), the up-to-date references are:

- `CHANGELOG.md` — released versions + unreleased PRs
- `docs/profiles-design.md` — active redesign (multi-provider, in progress)
- `docs/wave-5-design.md` — Part A segment mode (done), Part B image grid (backlog)
- `CONTRACTS.md` — frozen interface files + the additive / breaking change protocol
- `CLAUDE.md` — non-negotiable project regulations (Privacy, i18n, plain-language UI copy)

---

## How to spin up a worktree

1. From the main repo:
   ```bash
   git worktree add <workspace>/aitap-<name> -b wt/<name> main
   ```
2. New terminal in the new directory, `uv sync` to install Python deps, `pnpm install` under `src/aitap/ui/` if the work touches the frontend.
3. The standard four-gate bar before each commit:
   ```
   uv run pyright src/aitap
   uv run ruff check src/aitap tests
   uv run ruff format src/aitap tests --check
   uv run pytest tests/ -q
   ```
   Frontend gates when touching `src/aitap/ui/`:
   ```
   pnpm typecheck && pnpm lint && pnpm test && pnpm build
   ```
4. Per the established workflow: TDD → four gates → Opus 4.7 review → fix loop → confirm with maintainer → squash-merge PR.
5. Daily hygiene: `git fetch origin && git rebase origin/main`. When a frozen contract file changes upstream, rebase immediately and adapt.

---

## Active roadmap — multi-provider redesign

Replaces the hardcoded `Anthropic + OpenAI` duo with user-defined profiles (label + base_url + key + model_id + protocol). cc-switch-style UX. Four strict-serial worktrees, all in:

| # | Worktree | Status | What it added |
|---|---|---|---|
| 1 | `wt/profile-model` | ✅ **merged in PR #38** (commit `0c940d9`, 2026-06-01) | Profile / Defaults contract types · profile-id keyring API in `aitap.secrets` · `ProfileConfig` / `DefaultsConfig` config schema · YAML round-trip in `aitap.config_io` · CRUD routes (`GET/POST/PUT/DELETE/test`) · `PUT /api/settings/defaults` · slugify (Decision 2) · auto-null defaults on delete (Decision 1). Stage-disciplined: legacy `provider:` block and `/api/settings/key*` routes preserved. |
| 2 | `wt/profile-client` | ✅ **merged in PR #40** (commit `a8ba11e`, 2026-05-31) | LLM client factory dispatches on `profile.protocol` — `OpenAICompatClient` (mandatory `base_url`, covers DeepSeek / Kimi / MiMo / Groq / Together / OpenAI / SiliconFlow / Ollama / LM Studio); `AnthropicClient` gained kw-only `base_url` for Anthropic native (back-compat preserved). `aitap.secrets` import-discipline allow-list gained `server/routes/profiles.py` (route layer resolves the key and passes it into the factory; client classes never call `secrets`). `POST /api/profiles/{id}/test` stub replaced by a real minimal-call probe — 6 plain-language branches (ok / auth / rate_limit / network / other / no-key); PR #35 B2 anti-leak pattern preserved (static `detail` strings only, never `{exc}`). Pricing rows for DeepSeek / Moonshot / Groq llama-3.x / Together Llama-3.3 landed with source-URL citations; unknown models (Qwen / SiliconFlow / MiMo / Ollama / LM Studio) raise `UnknownModelError` → cost `unknown`, never silent `$0.00`. |
| 3 | `wt/profile-ui` | ✅ **merged in PR #42** (commit `0a053f1`, 2026-06-02) | Settings page rebuilt as a 3-section experience (642 → ~135 lines): Defaults card with two pickers sourced from `/api/profiles` → Profiles list with row-level Test / Edit / Delete / Set-as-default · Set-as-judge from a "..." menu → Add profile form with preset chip row + Manage presets dialog. `.aitap/profile-presets.json` seeds 11 starter rows on first launch (Anthropic / OpenAI / DeepSeek / Kimi / MiMo / Groq / Together / Qwen DashScope / SiliconFlow / Ollama / LM Studio); the dialog edits the file (add / edit / delete / reset-to-defaults). `MissingKeyBanner` switches data sources from `/api/settings.keys` to `/api/profiles` with two plain-language states. Security from PR #35 carried forward: API key inputs `type="password"` + `autoComplete="new-password"`, `setApiKey("")` runs in `finally`. a11y: Esc closes every confirm/edit dialog, initial focus on the safe action, draft-row keys use `crypto.randomUUID()`. en + zh i18n with parity test guard. New backend e2e canary `tests/integration/test_profiles_e2e.py` (3 tests) + Settings.test.tsx CANARY case pin the secrets contract for the profile-id flow. Hand-rolled `api/profiles.ts` types ahead of the codegen pass (cleanup runs it). Stage discipline: legacy `/api/settings/key*` routes + `Provider` enum still untouched — cleanup removes them. |
| 4 | `wt/profile-cleanup` | ✅ **merged in PR #43** (commit `b69e97b`, 2026-06-02) | Contract bumped to v3 with the breaking-change tag on `routes/__init__.py`. Removed pydantic models `ProviderKeyStatus` / `SetKeyRequest` / `TestKeyResponse` / `SettingsUpdate`, removed `SettingsResponse.keys`, removed routes `POST /api/settings/key`, `DELETE /api/settings/key/{provider}`, `POST /api/settings/test/{provider}`, `PUT /api/settings`. Removed `tests/unit/test_routes_settings_keys.py`, `tests/integration/test_secure_settings_e2e.py`, and the four `test_put_settings_*` cases in `test_api_runs.py`. Deleted the now-orphan `src/aitap/ui/src/api/settings-keys.ts` wrapper (no consumers left after PR #42). Regenerated `openapi.json` + `pnpm gen:api`. `CHANGELOG.md` carries the **BREAKING:** line. Scope discipline: `aitap.secrets` legacy provider-keyed API, the `register_provider` / `get_client` factory + `OpenAIClient`, `playground/dispatch.py` legacy default factory, and `config_io.py` legacy `provider:` block are deliberately untouched — `POST /api/runs` and `aitap scan --deep` still wire through them and migrating both is a separate follow-up worktree. |

All four serial worktrees in: the multi-provider redesign is the current contract. **Deep-scan migration finished in `0.1.0a4`**: `aitap scan --deep --profile <id>` now routes natively through `get_client_for_profile_config` via PR #58 (env-var bridge) and PR #61 (profile dispatch). The runs path (`POST /api/runs`) still rides the legacy provider-keyed dispatch and is the last remaining follow-up worktree.

---

## Active roadmap — image-prompt grid (Wave 5 Part B)

Adds a text-to-image surface so the user can compare DALL-E prompt outputs in an `N variants × M cases` grid. Three strict-serial worktrees per `docs/wave-5-design.md` §"Part B" / §"Worktree breakdown":

| # | Worktree | Status | What it added / will add |
|---|---|---|---|
| 1 | `wt/image-client` | ✅ **merged in PR #45** (commit hash filled at squash, 2026-06-03) | New `aitap.images` package parallel to `aitap.deep` (B·D1): `ImageClient` ABC + per-image-provider registry, `OpenAIImageClient` for DALL-E 2 + DALL-E 3 against `POST {base_url}/images/generations` (mandatory `base_url` + `api_key`, PR #40 OpenAICompatClient pattern), `MockImageClient` for offline tests, `aitap.images.factory.get_image_client_for_profile(profile, api_key)` dispatching on `profile.protocol` (`"openai-compat"` → `OpenAIImageClient`; `"anthropic"` → `ImageProviderError` with plain-language refusal), and `aitap.images.pricing` with DALL-E 2/3 rows tagged with source URL citations. PR #35 B2 anti-leak preserved: SDK exception bodies never reach `ImageProviderError.__str__`; static plain-language detail strings only. Stage discipline: no dispatch / no routes / no storage / no UI — `wt/image-dispatch` adds those next. |
| 2 | `wt/image-dispatch` | ⏳ **next** | Dispatch image runs through `aitap.images.factory`, store decoded bytes at `.aitap/runs/<id>/images/<case_index>_<variant>.png` (B·D3), surface the cost-confirmation gate (B·D4), record `image_path` on the `RunOutput` sidecar. New `POST /api/runs` arm or sibling endpoint for image runs (TBD by design doc Decision). |
| 3 | `wt/image-ui` | ⏳ **pending** | `N variants × M cases` grid page; click-cell-to-enlarge; per-column prompt + seed/params; no scoring widgets (B·D5 — vision judge is M5+1). en + zh i18n with parity test. |

---

## Tagged checkpoints (for navigation in git history)

- `wave-4-complete` → after PR #31 (the M4 self-iteration loop landed)
- `v0.1.0a4` → 2026-06-14 release: multi-provider redesign + cc-project (Pet Heaven) live-eval scanner work. PRs #32–#63 since `v0.1.0a3`.
- (no `wave-5-complete` yet — Part B image grid is backlog)

The tagged checkpoints exist so any session can `git checkout wave-N-complete` and see the codebase in the state that wave ended at, without bisecting.

---

## Conventions for contract-touching worktrees

When a worktree changes a frozen file in `CONTRACTS.md`:

1. Either go through the **additive protocol** (new fields default-valued, old shape preserved) or land an explicit **breaking change** with the version-comment header.
2. If breaking: surface a `BREAKING:` line in `CHANGELOG.md` under the next version's `Added` / `Changed` section.
3. After merge: regenerate `openapi.json` + `pnpm --dir src/aitap/ui run gen:api` so the TypeScript client tracks the contract. The codegen run uses the `sort_keys=True` + default `ensure_ascii` recipe documented in `docs/profiles-design.md` to avoid spurious-diff noise.

---

## Where the historical worktree briefs went

The pre-Wave-5 worktree details (Wave 1 scanner-core / infra / UI scaffold / CLI scaffold, Wave 2 dataflow / store / audit / providers / deep-scan, Wave 3 api-prompts / api-runs / runner / dataset / ui-inventory / ui-playground, Wave 4 judge / critic / impact / loop / iterations-store / api-iterate / ui-iterate) lived here in earlier revisions. Those branches were deleted after merge; the design intent for each wave is preserved in the matching `docs/wave-*-design.md`. The git log carries the per-commit story.
