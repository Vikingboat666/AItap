# Changelog

All notable changes to `aitap` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Vikingboat666/AItap/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/Vikingboat666/AItap/releases/tag/v0.1.0a1
