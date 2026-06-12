# aitap

> One-tap discovery and iteration for prompts in your AI codebase.

`aitap` ("AI Tap") is a zero-config CLI that scans any LLM-powered project, extracts every prompt and pipeline, and gives you a local Web Playground to test, iterate, and version them — before they ship.

## Why aitap

Prompts in real codebases are scattered across f-strings, templates, and config files. Today's debugging loop is reactive: you ship, users find regressions, you patch. Existing tools either require heavy yaml setup (Promptfoo), framework lock-in (DSPy), or live in production (LangSmith). Nobody solves the first mile: **"I don't even know all the prompts I have."**

`aitap` fixes that.

## What it does

- **Auto-discover** prompts and pipelines (DAG of LLM calls) in any Python project — zero config
- **Web Playground** for batch-running, comparing versions, and human-in-the-loop iteration
- **Self-iteration loop** powered by combined feedback (👍/👎, ideal answers, rules, LLM-as-judge)
- **Pipeline-aware**: detects RAG / agent / multi-step chains; warns about downstream impact when iterating a single node
- **Local-first**: all data stays on your machine; keys live in your OS keyring (preferred) or `~/.aitap/secrets.yaml` for headless setups
- **Multi-provider**: native support for Anthropic, OpenAI, DeepSeek, Moonshot (Kimi), MiMo, Groq, Together, Qwen DashScope, SiliconFlow, Ollama, LM Studio — anything speaking OpenAI-compatible or Anthropic protocol
- **Audit mode**: `aitap audit gh:owner/repo` to safely explore any open-source AI project

## Quickstart

```bash
# install (Python 3.10+) — pre-release, install from source
uv tool install git+https://github.com/Vikingboat666/AItap.git
# or: pipx install git+https://github.com/Vikingboat666/AItap.git

# inside your project
aitap init                       # creates ./.aitap/
aitap scan                       # discover prompts + pipelines (L1, no API calls)
aitap ui                         # open the web playground at http://127.0.0.1:7860 (use --port to change)
```

The scanner is **L1** by default — it reads your code as text and finds prompt call sites without ever calling out to an LLM, so it's free and offline. Some prompts are built at runtime (dispatchers, templates assembled from variables); L1 sees the call site but not the text. Pass `--deep` to add an **L2** pass that asks an LLM to summarise each opaque site — this calls your configured provider and costs money, so aitap prints the estimated USD and asks for confirmation first.

```bash
aitap scan --deep                       # uses your default profile
aitap scan --deep --profile <id>        # pick a specific profile
aitap scan --deep --yes                 # skip cost confirmation (scripted runs)
```

## Configuring a model provider

Two pieces: a **profile** in `.aitap/config.yaml` (label + endpoint + model) and a **key** in `~/.aitap/secrets.yaml` (or your OS keyring via the Settings UI).

### Option A: Settings UI (recommended)

`aitap ui` → Settings → Add profile → pick a preset (DeepSeek, Anthropic, OpenAI, …) → paste key → Test. Set one as the default and `aitap scan --deep` uses it automatically.

### Option B: edit `.aitap/config.yaml` by hand

```yaml
profiles:
  - id: deepseek
    label: DeepSeek
    base_url: https://api.deepseek.com/v1
    protocol: openai-compat
    model_id: deepseek-chat

defaults:
  model_profile_id: deepseek
```

`id` is whatever slug you pick — the Settings UI derives it from the label, in YAML you choose it directly. Keep it lower-case ASCII so the `profile:<id>:` secret key stays unambiguous.

Then put the key somewhere aitap can find:

- **OS keyring** (preferred — what the Settings UI's `Test` button writes to): Credential Manager on Windows, Keychain on macOS, Secret Service on Linux. The UI handles this for you.
- **File fallback** for headless setups (CI, containers without a keyring): create `~/.aitap/secrets.yaml`, set permissions to user-read-only (`chmod 600` on Unix, ACL on Windows), and add:
  ```yaml
  profile:deepseek: sk-replace-me
  ```

Profile-keyed dispatch routes through `OpenAICompatClient` (OpenAI / DeepSeek / Moonshot / MiMo / Groq / Together / Qwen DashScope / SiliconFlow / Ollama / LM Studio) or `AnthropicClient` (Anthropic + any Anthropic-compatible gateway) based on the `protocol` field.

The legacy `provider:` block in `.aitap/config.yaml` keeps working as a fallback for projects that haven't migrated to profiles.

## Audit mode

Scan any public GitHub repo without cloning it yourself:

```bash
aitap audit gh:owner/repo               # L1-only — audit never runs L2 on unknown code
aitap audit gh:owner/repo --keep-clone  # keep the temp clone so you can inspect it
```

Audit mode is L1-only by design: it will never spend your API key on third-party code. The clone goes into a temp directory and is removed on exit (unless `--keep-clone`); nothing about the target repo touches your project's `.aitap/`.

## Status

Pre-release (the unreleased changelog accumulates toward `0.1.0a4`). Active development.

See [`CHANGELOG.md`](CHANGELOG.md) for the full record.

## License

Apache 2.0
