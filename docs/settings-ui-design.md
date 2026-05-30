# Settings UI + secure API-key handling — design

Status: **approved.** Scope, security model, and contract changes are signed off; this doc is the reference `wt/settings-ui` builds against.

aitap today reads provider API keys from environment variables only. If a key is missing, the UI silently continues; if the user wants to set or change one, the only path is editing shell env / `.env`. That ships as a real product gap (no view, no input, no warning). This feature closes it without weakening the security posture.

## Scope

- A **Settings page** in the sidebar showing, per provider, whether a key is configured, where it lives, and a masked preview.
- **Inline key input** in Settings to save / replace / clear keys from the UI.
- A **"test connectivity"** action that issues one minimal LLM call per provider and surfaces the result.
- **Missing-key banners + inline prompts** on Inventory and Playground (and the deep-scan path) when the user tries to do something a key would unlock.
- All copy follows the CLAUDE.md *Plain-language UI copy* rule. All UI strings ship bilingual (en + zh) per the i18n rule; the locale-parity test guards completeness.

## Out of scope (this feature)

- Cloud sync of secrets (single-machine only).
- Per-project key overrides beyond the OS keyring's natural namespacing.
- Anything beyond the providers the codebase already supports (Anthropic, OpenAI).

## Security model — non-negotiable

The user's hard requirement: *keys must not leak by any path.*

### Storage

1. **Primary: the OS-native secret store**, via the [`keyring`](https://pypi.org/project/keyring/) package — the same mechanism `aws` / `gh` / `pip` use:
   - Windows → **Credential Manager**
   - macOS → **Keychain**
   - Linux → **Secret Service** (`libsecret`) / KWallet
   - Service name `aitap`; account `provider:<name>` (e.g. `provider:anthropic`).
   - Keys never land in a plaintext file under this path.
2. **Fallback (opt-in only): `~/.aitap/secrets.yaml`** with `0600` permissions on Unix / current-user-only ACL on Windows. Used only when `keyring.get_keyring()` reports an unusable backend (no Secret Service on a headless Linux, etc.). The UI shows an explicit confirmation before this fallback engages — never silent.
3. **Never the project tree.** No `aitap` secret ever lives under `.aitap/` or anywhere in the user's repo, regardless of fallback. (`.aitap/.gitignore` does *not* receive a `secrets.local.yaml` entry, because we don't write one.)

### Wire / API discipline

| Surface | Behaviour |
|---|---|
| `GET /api/settings` | Returns per-provider `{configured: bool, source: "keyring"|"fallback"|"env"|"none", masked: "sk-...XXXX"|null}`. **The raw key is never present in any GET response.** |
| `POST /api/settings/key` | Accepts `{provider, key}`. Persists via `keyring.set_password`. **The response body never echoes the key** — it returns the same `{configured, source, masked}` shape as GET. |
| `DELETE /api/settings/key/{provider}` | Calls `keyring.delete_password` (or removes the fallback file's entry). Returns `{configured: false}`. |
| `POST /api/settings/test/{provider}` | Issues a minimal call (Anthropic `/v1/messages` with `messages=[{"role":"user","content":"ping"}]`, `max_tokens=4`). Returns `{ok: true}` or `{ok: false, reason: "auth"|"rate_limit"|"network"|"other", detail: <plain sentence>}`. **Never returns the key.** |

### Code-path discipline

- A single module `aitap.secrets` owns all read/write/delete. Its public surface:
  - `get_key(provider) -> str | None` — the only function that returns the raw key. Marked `# secret-source` and **must not be imported outside the LLM-client construction path** (a unit test asserts this with `ast` scan over `src/aitap/`).
  - `key_status(provider) -> KeyStatus` — used by the API layer; returns the metadata struct, no raw key.
  - `set_key(provider, key)` / `delete_key(provider)` — write-side.
- The LLM `Client` accepts `api_key` only via constructor arg; the constructor takes the value from `secrets.get_key(...)` at the call site that already needs it.

### Log / persistence discipline

- A global `logging.Filter` registered at server startup walks each `LogRecord.msg`/`args` for `sk-...` / `xai-...` / `Bearer ...` patterns. Match → drop the record + emit a single sanitised warning. Test: emitting a fake key to a logger results in the record being dropped.
- A persistence test asserts no `prompts` / `pipelines` / `runs` / `iterations` / `prompt_versions` / `scores` row contains `sk-` or `Bearer `; a sidecar (`outputs.jsonl`) test does the same.
- CI step: `rg "sk-[A-Za-z0-9_-]{10,}" docs/ tests/fixtures/` (excluding documented placeholders like `sk-replace-me`) must return zero matches.

### Browser discipline

- Settings input field is `type="password"` + `autoComplete="new-password"`.
- On successful `POST /api/settings/key`, the React state holding the typed key is cleared in the same effect — only the masked preview from the response remains. The input is reset to empty.
- The `mutation` response is never logged to the console.

### Destruction path

The Settings page exposes a "Clear key" button per provider. Clicking it calls `DELETE /api/settings/key/{provider}`, which truly deletes (not overwrites) the entry. A test asserts a `GET /api/settings` immediately after returns `{configured: false}`.

## Contract changes (additive)

`src/aitap/server/routes/__init__.py` is a frozen contract — changes follow the CONTRACTS.md additive protocol.

```python
class ProviderKeyStatus(_ApiModel):
    provider: Literal["anthropic", "openai"]
    configured: bool
    source: Literal["keyring", "fallback", "env", "none"]
    masked: str | None  # "sk-ant-...XXXX" — last 4 chars; null when unconfigured

class SettingsResponse(_ApiModel):
    # ...existing fields stay byte-for-byte unchanged...
    keys: list[ProviderKeyStatus]   # NEW — additive

class SetKeyRequest(_ApiModel):
    provider: Literal["anthropic", "openai"]
    key: str

class TestKeyResponse(_ApiModel):
    ok: bool
    reason: Literal["auth", "rate_limit", "network", "other"] | None = None
    detail: str | None = None       # plain-language sentence
```

The existing `providers_available: list[ProviderEvidence]` field stays — it surfaces env-detected providers from the *scan* of the user's codebase, which is a different signal from the keys aitap holds.

## UI surface

- **Sidebar**: new `Settings` item between `Audit` and the footer (i18n keys `sidebar.settings`).
- **Settings page** (`pages/Settings.tsx`): one card per provider with `{configured, source, masked, last-tested-at}`, a password input + "Save" button, "Test" button, "Clear" button. Plain-language status copy: *"No key set — runs that need Anthropic will be skipped."* / *"Saved to system keychain. Last tested OK."*
- **Missing-key banners**:
  - Inventory header (one persistent banner when no provider has a key): *"No API key is set. Some features (deep scan, runs) won't work until you add one in Settings."* with a link to `/settings`.
  - Playground Run button area, when the resolved `provider` has no key: inline alert *"This run needs an {{provider}} key. Add one in Settings."* (does not disable Run for prompt-edit-only workflows; surfaces the inevitable error before it happens).
  - Deep-scan CLI path (`aitap scan --deep`): if the resolved provider lacks a key, exit early with a plain sentence pointing at `aitap ui → Settings` *or* setting the env var.

## Worktree breakdown

Single worktree: **`wt/settings-ui`**. Suggested commit checkpoints (so a sub-agent stalling mid-task preserves progress):

1. `aitap.secrets` module + tests (keyring backend + fallback + the `ast`-scan import-discipline test + the logging filter + the persistence-leak tests).
2. Backend API: `GET /api/settings` extension + `POST /api/settings/key` + `DELETE` + `POST /api/settings/test/{provider}` + route tests. Contract additive change.
3. Frontend Settings page + LanguageSwitcher-style state handling + zero-key clearing + missing-key banner on Inventory + inline alert on Playground. i18n keys in en+zh.
4. `pnpm gen:api` regen + final wire-up + e2e flow tests.

## Testing

- Backend: keyring round-trip (mock backend), fallback round-trip, GET/POST/DELETE/TEST endpoints, **never-echo-key** assertions on every response shape, log-filter test, persistence-leak scan.
- Frontend: Settings page loads + shows configured/unconfigured states, save flow clears the input + shows masked, test button shows result, clear button removes; missing-key banner appears/disappears based on `keys` array; i18n parity passes.
- Cross-cutting: a top-level integration test that **fakes a `sk-fake-anthropic`**, exercises save → test → run-a-prompt → delete, then greps the entire `.aitap/` tree + all server response bodies for the literal key — must not appear except in the outgoing LLM call (which is mocked).

## Open questions / future work

- A second-machine sync story (out of scope here; would need an explicit user-chosen encrypted vault).
- Per-project override (today the keyring is user-global; if a user wants different keys per project later, they can override via env var, which our resolver already respects).
- Web UI for cost budgets (the existing `cost_per_run_usd` / `cost_per_session_usd` config is API-only today — separate follow-up).
