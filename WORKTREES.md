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

Replaces the hardcoded `Anthropic + OpenAI` duo with user-defined profiles (label + base_url + key + model_id + protocol). cc-switch-style UX. Four strict-serial worktrees:

| # | Worktree | Status | What it adds |
|---|---|---|---|
| 1 | `wt/profile-model` | ✅ **merged in PR #38** (commit `0c940d9`, 2026-06-01) | Profile / Defaults contract types · profile-id keyring API in `aitap.secrets` · `ProfileConfig` / `DefaultsConfig` config schema · YAML round-trip in `aitap.config_io` · CRUD routes (`GET/POST/PUT/DELETE/test`) · `PUT /api/settings/defaults` · slugify (Decision 2) · auto-null defaults on delete (Decision 1). Stage-disciplined: legacy `provider:` block and `/api/settings/key*` routes are preserved. |
| 2 | `wt/profile-client` | ⏳ **next** | LLM client factory dispatches on `profile.protocol` — `OpenAICompatClient` (DeepSeek / Kimi / MiMo / Groq / Together / OpenAI / Ollama / LM Studio / ...) with mandatory `base_url`; `AnthropicClient` with explicit `base_url` for Anthropic native. `aitap.secrets` import-discipline allow-list gains the LLM-client construction sites that may call `get_key_for_profile`. The `POST /api/profiles/{id}/test` stub becomes a real minimal-call probe. Pricing rows for the well-known third parties land in `aitap.deep.pricing`; unknown models return cost `None` (UI shows `cost: unknown`, never silent `$0.00`). |
| 3 | `wt/profile-ui` | ⏳ pending | Settings page rebuild around three sections: **Defaults** (two pickers sourcing from the profile pool) → **Profiles list** (row per profile with masked key + Test + Edit + Delete) → **Add profile** form with preset-template chips. Seeds `.aitap/profile-presets.json` with the 11 starter rows on first launch and ships a **Manage presets** editor (Decision 4). Updates Inventory's missing-key banner to point at the new flow. i18n: en + zh, locale-parity test guards completeness. |
| 4 | `wt/profile-cleanup` | ⏳ pending | Deletes `Provider = Literal["anthropic", "openai"]`, `ProviderKeyStatus`, `SetKeyRequest`, `TestKeyResponse` from the contract, deletes the legacy `POST/DELETE /api/settings/key*` and `POST /api/settings/test/{provider}` routes, deletes the legacy keyring `provider:<name>` callers. Pins `# Contract version: 3 (YYYY-MM-DD) — breaking change: provider enum → profiles list` on `routes/__init__.py`. Lands the `BREAKING:` line in `CHANGELOG.md`. Regenerates `openapi.json` + `pnpm gen:api`. Tier-2 user-facing doc cleanup (README / quickstart / architecture) ships in this worktree too. |

Order is strict: each step depends on the previous. UI doesn't ship until clients work; cleanup doesn't ship until UI is on the new model.

---

## Tagged checkpoints (for navigation in git history)

- `wave-4-complete` → after PR #31 (the M4 self-iteration loop landed)
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
