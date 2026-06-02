# Multi-provider profile redesign — design

Status: **Partial.** Three of four worktrees landed: `wt/profile-model` shipped in PR #38 (Profile/Defaults contract + keyring API + config schema + CRUD routes), `wt/profile-client` shipped in PR #40 (protocol-dispatching LLM client factory + real probe + pricing), and `wt/profile-ui` shipped in PR #42 (Settings page 3-section rebuild + preset editor + Inventory banner switch + en/zh i18n + e2e canary). `wt/profile-cleanup` is the last step — it removes the legacy `Provider` enum + `/api/settings/key*` routes + the hand-rolled `settings-keys.ts` wrapper, bumps the contract to v3, and regenerates `openapi.json`. Until that lands, the legacy backend surface is dead code (no UI consumer left after PR #42) but still present. All four open questions resolved (see Decision log at the bottom); this doc is the reference cleanup builds against. Note: the `Profile` data model below lists `key_source` as `Literal["keyring", "fallback", "env", "none"]`, but the implemented `routes/__init__.py` Profile narrows to `"keyring" | "fallback" | "none"` (profile-id keys don't come from env vars — `env` was a legacy provider-keyed concept).

The current Settings page hardcodes Anthropic + OpenAI. Almost every other LLM endpoint a working developer cares about — DeepSeek, Moonshot/Kimi, MiMo, Groq, Together, Qwen DashScope, SiliconFlow, local vLLM / Ollama / LMStudio — speaks the OpenAI chat-completions protocol. Forcing each one through a hardcoded "provider" enum throws that whole ecosystem away.

This document specifies the redesign that closes the gap. Inspired by the cc-switch UX: arbitrary user-defined **profiles**, each its own endpoint+key+model triple, with two selectors at the top picking the default + judge from the configured pool.

## Scope

- Replace the hardcoded `Provider = Literal["anthropic", "openai"]` enum with a list of user-defined **profiles**.
- Each profile carries: `id` (slug, stable), `label` (display, free-text, user-editable), `base_url`, `api_key` (in OS keyring), `model_id`, `protocol` (`"openai-compat"` default | `"anthropic"`).
- Settings page becomes: **Defaults** (two pickers) → **Profiles list** (rows with masked key + test + edit + delete) → **Add profile** (form + preset template chips).
- LLM client factory dispatches on `protocol`, not on a hardcoded provider name.
- Connectivity test (`POST /api/profiles/{id}/test`) sends a minimal call against the profile's actual base_url.

## Out of scope (this redesign)

- A multi-tenant / shared-team profile store (single-user, single-machine).
- Cost estimation for unknown providers — we ship pricing rows only for the providers we have rates for; unknowns render `cost: unknown` in the UI rather than 0.
- Per-profile rate-limit awareness.
- Streaming chat or non-chat endpoints (embeddings, image, audio) — separate effort.

## Why "blank slate" (user decision 1)

We do **not** auto-seed anthropic/openai placeholder rows on first launch. Rationale:
- Conceptual cleanliness: every row is a user-configured profile; there's no "magic two" to explain.
- The preset-template chips on the Add form (see below) make adding Anthropic or OpenAI the same single click as adding DeepSeek or Kimi — there's no convenience lost.
- Existing users who already ran an earlier alpha get a one-line warning on startup: "Your old config.yaml predates the multi-profile redesign. Re-add your keys in Settings." A `breaking` note lands in `CHANGELOG.md`.

## Preset templates (user-editable)

The Add Profile form has a "Start from a template" row of chips. Clicking one pre-fills `base_url` + `protocol` (+ a sensible default `model_id`); user provides label + key.

**The preset list is user-managed**, not hardcoded in the binary. We seed `.aitap/profile-presets.json` with the 11 below on first launch; the user can edit that file directly OR use a "Manage presets" link next to the chip row that opens a small editor (add / edit / delete / reset-to-defaults). This way a new vendor (or a user's private gateway) can be a preset without waiting for an aitap release.

Seeded set:

| Chip | base_url | protocol | suggested model_id |
|---|---|---|---|
| Anthropic | `https://api.anthropic.com` | `anthropic` | `claude-sonnet-4-6` |
| OpenAI | `https://api.openai.com/v1` | `openai-compat` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `openai-compat` | `deepseek-chat` |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` | `openai-compat` | `moonshot-v1-32k` |
| MiMo (Xiaomi) | `https://api.xiaomi.com/openai/v1` | `openai-compat` | `mimo-7b-rl` |
| Groq | `https://api.groq.com/openai/v1` | `openai-compat` | `llama-3.1-70b-versatile` |
| Together | `https://api.together.xyz/v1` | `openai-compat` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| Qwen / DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `openai-compat` | `qwen2.5-72b-instruct` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `openai-compat` | `Qwen/Qwen2.5-72B-Instruct` |
| Ollama (local) | `http://127.0.0.1:11434/v1` | `openai-compat` | `llama3.1` |
| LM Studio (local) | `http://127.0.0.1:1234/v1` | `openai-compat` | (whatever you loaded) |

Templates are **suggestions**, not gates. The user can edit every field of a template-spawned profile or add a profile fully from scratch.

## Data model

### Profile (the new core entity)

```python
class Profile(_ApiModel):
    id: str          # stable slug, derived from label on creation
    label: str       # display name, user-editable
    base_url: str    # full URL with /v1 or whatever the vendor uses
    protocol: Literal["openai-compat", "anthropic"]
    model_id: str    # default model for this profile
    notes: str = ""  # free-form, e.g. "billing capped at $5/mo"
    # The API never returns the raw key. Status fields mirror PR #35:
    key_configured: bool
    key_source: Literal["keyring", "fallback", "env", "none"]
    key_masked: str | None  # "sk-...XXXX"
```

### Defaults

```python
class Defaults(_ApiModel):
    model_profile_id: str | None         # which profile to use for runs / --deep
    judge_profile_id: str | None         # which profile to use for the judge; null = reuse model_profile_id
```

### SettingsResponse (additive-breaking — see Contracts section)

```python
class SettingsResponse(_ApiModel):
    profiles: list[Profile]              # NEW — the user's configured pool
    defaults: Defaults                   # NEW
    # Legacy fields retained for backward-compat reading only — no longer
    # surface in UI. We keep the *shape* so existing API consumers don't
    # 422 on parse, but the UI never reads them after this PR lands.
    provider: str | None                 # mirror of defaults.model_profile_id (legacy alias)
    model: str | None                    # mirror of the default profile's model_id
    judge_model: str | None
    cost_per_run_usd: float
    cost_per_session_usd: float
    providers_available: list[ProviderEvidence]
    # `keys` from PR #35 is removed: per-profile status now lives inline on Profile.
```

### Single-worker assumption

The route layer caches the active profile list + defaults in module-level state (``aitap.server.routes.profiles._PROFILES`` / ``_defaults``). That cache assumes ``aitap ui`` runs the FastAPI app under a single uvicorn worker — the default and only documented deployment mode. A multi-worker deployment would need a shared store (Redis, SQLite, …) because each worker would otherwise hold its own drifting copy. Multi-worker is out of scope.

### Storage

| Bit | Lives in | Why |
|---|---|---|
| profile.label, base_url, protocol, model_id, notes | `.aitap/config.yaml` → `profiles: [...]` | Survives restarts, version-controllable, no secrets |
| profile.api_key | OS keyring (Windows Credential Manager / macOS Keychain / Secret Service) under `service="aitap"`, `account=f"profile:{profile.id}"` | Sole storage path the security model from PR #35 permits |
| defaults | `.aitap/config.yaml` → `defaults: {model_profile_id: ..., judge_profile_id: ...}` | Same persistence story as profile metadata |

## Contract changes

`src/aitap/server/routes/__init__.py` is a frozen contract file. This redesign **breaks** the legacy `Provider` enum and `SetKeyRequest`/`SettingsResponse.keys` shape — we go through the additive protocol for what we can, and explicitly mark the breaking removal of `keys: list[ProviderKeyStatus]` so downstream consumers know.

**Net contract delta:**
- New: `Profile`, `Defaults`, `ProfileUpsertRequest`, `ProfileTestResponse`.
- New routes: `GET /api/profiles`, `POST /api/profiles`, `PUT /api/profiles/{id}`, `DELETE /api/profiles/{id}`, `POST /api/profiles/{id}/test`, `PUT /api/settings/defaults`.
- Legacy `keys: list[ProviderKeyStatus]` field on `SettingsResponse` is **removed**. (Pre-1.0 alpha, no external clients exist; surfaced as a `BREAKING:` line in CHANGELOG.)
- Legacy routes from PR #35 (`POST /api/settings/key`, `DELETE /api/settings/key/{provider}`, `POST /api/settings/test/{provider}`) are **removed** (alpha-stage; replaced by `/api/profiles` routes).
- Tag a `# Contract version: 3 (2026-MM-DD) — breaking change: provider enum → profiles list` header at the top of the contract file per CONTRACTS.md protocol.

## Backend architecture

### LLM client construction

`aitap.deep.client.get_client_for_profile(profile)` becomes the new entry point. It dispatches:

```python
def get_client_for_profile(profile: Profile, api_key: str) -> LLMClient:
    if profile.protocol == "anthropic":
        return AnthropicClient(
            model=profile.model_id, api_key=api_key, base_url=profile.base_url,
        )
    # default: OpenAI-compatible (DeepSeek, Kimi, MiMo, Groq, Together, ...).
    return OpenAICompatClient(
        model=profile.model_id, api_key=api_key, base_url=profile.base_url,
    )
```

`OpenAICompatClient` is a renamed `OpenAIClient` from PR #35, with a mandatory `base_url` arg. The official OpenAI endpoint just becomes one specific profile (`https://api.openai.com/v1`). `AnthropicClient` similarly gains an explicit `base_url`.

### Connectivity test

`POST /api/profiles/{id}/test` resolves the profile, builds the client, and sends one minimal call:
- `openai-compat`: `POST {base_url}/chat/completions`, `messages=[{role:user, content:"ping"}]`, `max_tokens=4`.
- `anthropic`: `POST {base_url}/v1/messages`, same body shape.

Response is `{ok: bool, reason: "auth"|"rate_limit"|"network"|"other"|null, detail: <plain-language sentence>}` — exactly the same shape as PR #35's test endpoint, just keyed by `profile_id` instead of provider enum.

### secrets module changes

`aitap.secrets` (PR #35) keeps the keyring service name `aitap`. The account convention switches:
- Old: `provider:anthropic`, `provider:openai`
- New: `profile:{profile.id}`

`get_key(profile_id)` replaces `get_key(provider)`. The import-discipline test from PR #35 is retained with `profile_id` as the new parameter type. The log filter is unchanged (it pattern-matches `sk-…` / `Bearer …` regardless of attribution).

Migration is **blank-slate**: there's no automatic conversion of old keyring entries. The first time a user opens the new Settings page they re-add their key, which writes a new keyring entry under the new account convention. The old entries become orphan and the user can delete them via Windows' Credential Manager / Keychain directly (we'll mention this in the changelog).

### Cost handling

`src/aitap/deep/pricing.py` keeps the rows it has for OpenAI and Anthropic models, and gains rows for the well-known third-party providers as we can confirm rates from public docs (DeepSeek, Moonshot, Together, Groq). For any model not in the table, `estimate_cost` returns `None`, and the UI shows `cost: unknown` instead of `$0.0000`. No silent zero — that's misleading for paid usage.

## Frontend redesign

Settings page rebuild around three sections:

### 1. Defaults card (top)

- Two combobox-style `<select>`s, both sourced from `profiles`:
  - "Default model" — required when there's ≥1 profile; the dropdown shows `{label} · {model_id}` per option.
  - "Judge model" — optional; empty means "reuse default". Same dropdown.
- Save button → `PUT /api/settings/defaults`.

### 2. Profiles list

- One row per profile, sortable by `label`.
- Each row: `{label}` (clickable to edit) · masked key · "Test" button · "..." menu (Edit, Delete, Set as default).
- Empty state: "No profiles yet. Add one below to get started."

### 3. Add Profile form

- Always-visible card at the bottom (or modal — TBD by UI prototyping).
- Fields: label (required, free text), base_url (required), api_key (`type=password`), model_id (required), protocol (radio).
- Above the fields: a row of preset template chips. Click one → pre-fills base_url + protocol + a sensible model_id; the user still picks a label and pastes a key.

### Plain-language + i18n + a11y discipline

All new copy lands in en + zh, locale-parity test guards completeness, no jargon (e.g. *"Add a model endpoint"* instead of *"Configure provider profile"*). Confirm dialogs on delete (with the same `role="dialog"` aria-modal pattern from PR #35). Per-row test result rendered inline with a `role="status"` region.

## Test matrix

### Backend
- Profile round-trip: create → list → update → delete; ID stays stable across label edits.
- Keyring account naming: profile creation writes under `profile:{id}` exactly; delete actually removes the entry.
- Defaults endpoint validates references (PUT with unknown profile_id → 422 + plain-language detail).
- Cannot delete a profile referenced by `defaults` without first unsetting it (or auto-shift defaults → null, decision pending — see Open Questions).
- Connectivity test issues exactly one POST to `{base_url}/{path}` with the right Authorization header per protocol, and the response never echoes the key.
- e2e canary test (mirroring PR #35's `test_secure_settings_e2e.py`): plant `sk-fake-profile-canary` in one profile, exercise list/get/test/delete, grep every response body + log line + sqlite column + `.aitap/` file + `~/.aitap/secrets.yaml` — must not appear.

### Frontend
- Add → Edit → Delete a profile via the UI.
- Click a preset template → fields populate correctly.
- Defaults dropdown reflects the live profile list; deleting the default profile triggers the warning flow.
- Connectivity test renders ok / err with plain-language detail.
- Cannot save a profile with a blank label or base_url.
- en + zh both render correctly; locale-parity test passes.

## Worktree breakdown — proposed

| # | Worktree | Scope | Depends on |
|---|---|---|---|
| **1** | `wt/profile-model` | New `Profile` / `Defaults` / contracts in `routes/__init__.py`; new `config.yaml` schema reader / writer; `secrets.py` account convention change; basic CRUD endpoints | main |
| 2 | `wt/profile-client` | `OpenAICompatClient` (rename + base_url), `AnthropicClient` gets explicit base_url, factory dispatches on `protocol`; pricing table for the new presets | profile-model |
| 3 | `wt/profile-ui` | Settings page rebuild (DefaultsCard rewrite, ProfilesList, AddProfileForm + preset chips), **"Manage presets" editor + `.aitap/profile-presets.json` seed-on-launch logic**, i18n keys, e2e canary test | profile-client |
| 4 | `wt/profile-cleanup` | Remove legacy `keys`/`SetKeyRequest`/`/api/settings/key` routes + their tests, update CHANGELOG with the `BREAKING:` note, regen `openapi.json` + `pnpm gen:api` | profile-ui |

Suggested order: 1 → 2 → 3 → 4 (strict). Each is Opus-reviewable and can land independently.

## Decision log

| # | Question | Decision | Reasoning |
|---|---|---|---|
| 1 | Deleting the profile currently set as `defaults.model_profile_id` | **Auto-null the default + render a yellow Inventory-top banner** "No default model is set — open Settings to pick one." | Doesn't block the delete (reversible by the user), but the banner makes the resulting state visible. Same pattern as the existing missing-key banner from PR #35 — UX-coherent. |
| 2 | Slug algorithm for `profile.id` derived from `label` | **Lower-case ASCII slug**: strip diacritics with NFKD, drop non-alphanumeric (keeping `-` and `_`), collapse repeated `-`. On collision append `-2`, `-3`, … | IDs end up in keyring account names and filesystem-adjacent contexts where Unicode causes subtle bugs across platforms (Windows Credential Manager, dbus). Labels stay Unicode-rich for display. |
| 3 | Connectivity-test request shape for `protocol="anthropic"` | `POST {base_url}/v1/messages` with `messages=[{role:"user", content:"ping"}]`, `max_tokens=4` | Same shape as the `openai-compat` branch (saves a code path), and Anthropic's per-token billing on `messages` is cheaper than a system+messages call. |
| 4 | Preset templates list | **User-editable.** Seed `.aitap/profile-presets.json` with the 11 above on first launch; user can edit the file OR use a "Manage presets" link next to the chip row | Vendors come and go fast — locking the list in code means every new endpoint waits on an aitap release. Cost is a small editor UI and a JSON file. Users who never touch it see the same defaults we ship. |

## References

- `docs/wave-5-design.md` — prior wave; reference for the design-doc format.
- `CLAUDE.md` — Privacy, i18n, Plain-language UI copy regulations.
- `CONTRACTS.md` — additive protocol + breaking-change procedure.
- PR #35 — secure key management foundation this builds on.
- PR #37 — the current (limited) defaults UI being replaced.
