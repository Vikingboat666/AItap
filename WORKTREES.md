# Parallel Worktree Missions

This document tracks the parallel worktrees and the Claude prompts to bootstrap a session in each one. The full development plan lives at `C:\Users\1\.claude\plans\llm-humming-pike.md`.

---

## How to start a Claude session in a worktree

1. Open a new terminal **in the worktree directory** (not in main).
2. Run `claude` to start Claude Code.
3. Copy-paste the corresponding "Claude prompt" block from below.
4. When the worktree's mission is complete, the work is committed on its branch — open a PR back to `main`.

Daily hygiene per worktree:

```bash
git fetch origin
git rebase origin/main
```

If a contract file (see `CONTRACTS.md`) changes upstream, rebase right away and adapt.

---

## Wave 1 (now)

| Worktree | Branch | Path |
|---|---|---|
| Scanner core (M1) | `wt/scanner-core` | `D:/AIcoding/aitap-scanner-core` |
| Infrastructure | `wt/infra` | `D:/AIcoding/aitap-infra` |
| UI scaffold | `wt/ui-scaffold` | `D:/AIcoding/aitap-ui-scaffold` |
| CLI scaffold | `wt/cli-scaffold` | `D:/AIcoding/aitap-cli-scaffold` |

---

## wt/scanner-core — Scanner core (M1)

**Goal**: Ship the L1 rule-based scanner so `aitap scan` can find every prompt in a Python project.

**In scope**

- `src/aitap/scanner/engine.py` — orchestrator that walks the project tree, dispatches to language adapters, aggregates `ScanResult`
- `src/aitap/scanner/languages/python.py` — Python AST traversal + tree-sitter fallback for unparseable files
- `src/aitap/scanner/rules/sdk_calls.py` — known signatures for openai and anthropic SDKs (minimum); structure for adding more
- `src/aitap/scanner/rules/prompt_extractor.py` — extract string literals, f-strings, multi-line strings, basic jinja2 templates from call args
- `src/aitap/scanner/rules/env_inspector.py` — detect `.env` and config files; identify which providers are configured (key existence only, never read values)
- `tests/fixtures/openai_basic/` — sample project with 2-3 OpenAI ChatCompletion calls
- `tests/fixtures/anthropic_agent/` — sample project with an Anthropic call wrapped in a function
- `tests/unit/test_engine.py`, `test_python_lang.py`, `test_sdk_calls.py`, `test_prompt_extractor.py`, `test_env_inspector.py`
- Markdown terminal report rendered via `rich`
- A `scan` subcommand factory exported from `scanner/__init__.py` (so `wt/cli-scaffold` can wire it without circular imports)

**Out of scope** — do NOT touch

- `src/aitap/scanner/dataflow/` (Pipeline detection — Wave 2)
- `src/aitap/scanner/models.py` (frozen contract)
- `src/aitap/cli.py` (CLI wiring belongs to `wt/cli-scaffold`)
- `src/aitap/store/` (storage — Wave 2)
- `src/aitap/deep/` (L2 — Wave 2 / Wave 5)

**Acceptance criteria**

- `python -m aitap.scanner.engine tests/fixtures/openai_basic` produces a `ScanResult` with ≥ 2 `PromptSite`s
- `pytest tests/unit/test_*.py -k scanner` all green
- pyright strict on the new files (no errors)

**Claude prompt** (paste verbatim into the worktree):

```
我现在在 wt/scanner-core 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/scanner-core" 节的 In scope / Out of scope / Acceptance criteria 实现 M1 的 L1 规则扫描器。

开始前请先读：
1. CONTRACTS.md（理解契约边界）
2. src/aitap/scanner/models.py（你输出的数据形状）
3. C:\Users\1\.claude\plans\llm-humming-pike.md 中 M1 部分

实现过程中：
- 严格不动 Out of scope 列出的文件
- 每个新文件配套单测
- 写完 ruff/pyright 都要过
- 完成后单 commit 提交，commit message 用 conventional commits 风格

如果发现 scanner/models.py 有缺失字段需要新增（不能修改已有字段），先停下来跟我确认走 CONTRACTS.md 流程。
```

---

## wt/infra — Infrastructure & docs

**Goal**: Make the project credible-looking and easy to contribute to. Get CI green on every push.

**In scope**

- `.github/workflows/ci.yml` — matrix: Python 3.10/3.11/3.12 × ubuntu-latest/macos-latest/windows-latest; runs ruff, pyright, pytest
- `.github/workflows/release.yml` — on tag `v*`, builds and publishes to PyPI via OIDC trusted publishing
- `.github/ISSUE_TEMPLATE/` — bug report, feature request, scanner false-positive
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/dependabot.yml`
- `docs/index.md`, `docs/quickstart.md`, `docs/architecture.md`, `docs/rules/index.md`
- `docs/mkdocs.yml` (or root `mkdocs.yml`) — material theme, navigation
- `examples/starter/` — minimal Python project that uses OpenAI + Anthropic so users can try `aitap scan` against it
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
- `Makefile` (or `justfile`) with targets: `install`, `test`, `lint`, `format`, `docs`, `build`

**Out of scope**

- Anything under `src/aitap/` other than the example fixture
- Don't write CI that depends on yet-to-exist features (scanner, server, ui) — keep CI to lint + pyright + pytest only

**Acceptance criteria**

- Push triggers CI; CI passes on all 9 matrix cells with the empty test suite
- `mkdocs serve` from the docs dir renders the site locally
- `make test` runs the test suite
- The example project under `examples/starter/` actually executes (with mocked LLM call) so we can dogfood it

**Claude prompt**:

```
我现在在 wt/infra 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/infra" 节的 In scope / Out of scope / Acceptance criteria 实现 OSS 项目基础设施。

开始前读：
1. README.md
2. pyproject.toml（理解依赖结构）
3. C:\Users\1\.claude\plans\llm-humming-pike.md 中"开源运营"和"实现里程碑"

约束：
- CI 只跑 lint + pyright + pytest，不要尝试构建 wheel 或前端（那些待 wt/ui-scaffold + 后续 milestone）
- mkdocs 用 material 主题
- examples/starter 要能 import 但 LLM 调用部分用 mock
- 完成后单 commit，conventional commits 风格
```

---

## wt/ui-scaffold — UI scaffolding

**Goal**: Stand up a Vite + React + Tailwind frontend skeleton with mocked data, so frontend feature worktrees can ship pages without waiting for backend.

**In scope**

- `src/aitap/ui/package.json` — Vite + React 18 + TypeScript + Tailwind 3 + react-router-dom + react-flow + tanstack/react-query
- `src/aitap/ui/vite.config.ts` — output to `../server/static/`, proxy `/api` to `localhost:7860` in dev
- `src/aitap/ui/tsconfig.json`, `tsconfig.node.json`
- `src/aitap/ui/tailwind.config.ts`, `postcss.config.js`
- `src/aitap/ui/index.html`
- `src/aitap/ui/src/main.tsx`, `App.tsx`
- `src/aitap/ui/src/router.tsx` — routes for /, /prompts/:id, /pipelines/:id, /playground, /history, /audit
- `src/aitap/ui/src/components/Layout.tsx`, `Sidebar.tsx`, `Header.tsx`
- `src/aitap/ui/src/api/client.ts` — fetch wrapper with `/api` base
- `src/aitap/ui/src/api/types.ts` — placeholder until openapi-typescript-codegen is wired
- `src/aitap/ui/src/api/mock.ts` — canned `ScanResult`-like fixtures for development
- `src/aitap/ui/src/pages/Inventory.tsx`, `PromptDetail.tsx`, `PipelineDetail.tsx`, `Playground.tsx`, `History.tsx`, `Audit.tsx` — placeholder components rendering mock data
- `src/aitap/ui/src/pages/components/DagView.tsx` — react-flow DAG (renders mock pipeline)
- pnpm scripts: `dev`, `build`, `lint`, `format`, `gen:api` (placeholder calling openapi-typescript-codegen)
- `.gitignore` additions for ui-specific build artifacts (already covered, verify)

**Out of scope**

- Backend / FastAPI (`wt/cli-scaffold` will wire `aitap ui` later; backend itself is M3)
- Real API integration (use mocks until backend exists)
- Wheel-bundling logic (that's the hatch-build hook in M5/release)

**Acceptance criteria**

- `pnpm install && pnpm dev` from `src/aitap/ui` opens a working SPA on localhost:5173 (Vite default)
- All 6 page routes render placeholder content
- DAG view renders a 3-node example pipeline from mock data
- `pnpm build` produces `../server/static/` output
- Tailwind classes work in components

**Claude prompt**:

```
我现在在 wt/ui-scaffold 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/ui-scaffold" 节的 In scope / Out of scope / Acceptance criteria 搭建 React 前端骨架。

开始前读：
1. CONTRACTS.md
2. src/aitap/server/routes/__init__.py（前后端共享的 API 形状，先用它生成 TS 类型；如果 openapi-typescript-codegen 现在不能跑就先手抄一份占位）
3. C:\Users\1\.claude\plans\llm-humming-pike.md 中前端相关章节

实现要求：
- Vite + React 18 + TS + Tailwind 3 + react-router-dom v6 + react-flow + tanstack/react-query
- 所有页面用 mock 数据先跑起来
- 不动 src/aitap/ 之外的目录（除 .gitignore 调整）
- pnpm 不是 npm
- 完成后单 commit
```

---

## wt/cli-scaffold — CLI subcommand wiring

**Goal**: Define every CLI command's surface (signatures, help text, options) without implementing the heavy logic yet — those bodies get filled in by other worktrees.

**In scope**

- Extend `src/aitap/cli.py` with subcommands:
  - `aitap init` — fully implement (writes `.aitap/{prompts,pipelines,datasets,runs}/`, `.aitap/config.yaml`, appends to `.gitignore`)
  - `aitap scan [path] [--rules-only|--deep]` — defines flags, calls placeholder `scanner.engine.scan_project()` if exists else stub
  - `aitap audit <repo>` — defines flag for `gh:owner/repo` shorthand, stub body
  - `aitap ui [--port=7860]` — stub that prints "not yet implemented"
  - `aitap diff <prompt> <v1> <v2>` — stub
  - `aitap rollback <prompt> <version>` — stub
- All commands use `rich` for output
- `tests/unit/test_cli.py` — invoke each command via `typer.testing.CliRunner`, assert exit codes and basic help strings

**Out of scope**

- Don't implement scanner / runner / iteration logic — call placeholder factories that other worktrees will provide
- Don't touch `cli.py` callbacks belonging to other modules

**Acceptance criteria**

- `aitap --help` lists all 6 subcommands with one-line descriptions
- `aitap init` in an empty dir creates the full `.aitap/` skeleton
- `pytest tests/unit/test_cli.py` green
- `aitap scan --help` shows `--rules-only` and `--deep` flags

**Claude prompt**:

```
我现在在 wt/cli-scaffold 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/cli-scaffold" 节的 In scope / Out of scope / Acceptance criteria 实现 CLI 子命令骨架。

开始前读：
1. src/aitap/cli.py（已有的 root callback）
2. src/aitap/config.py
3. CONTRACTS.md
4. C:\Users\1\.claude\plans\llm-humming-pike.md 中"CLI 命令集"

约束：
- aitap init 是唯一要完整实现的命令；其余命令只搭骨架（签名+help+rich-styled "not yet implemented" 输出 或 调一个会被别人填的 placeholder）
- 用 typer.testing.CliRunner 写测试
- 完成后单 commit
```

---

## Coordination

**Daily**: each worktree rebases on `origin/main` first thing.

**Contract changes**: see `CONTRACTS.md` — single PR, broadcast to all worktrees, everyone rebases.

**Wave 1 → Wave 2 sync**: when all 4 worktrees merge, tag `wave-1-complete` on main and proceed to Wave 2 worktree creation (see plan).
