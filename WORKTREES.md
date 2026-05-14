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

## Wave 1 — complete (tag `wave-1-complete`)

| Worktree | Branch | PR |
|---|---|---|
| Scanner core (M1) | `wt/scanner-core` | #1 |
| Infrastructure | `wt/infra` | #2 + hotfix #9 |
| UI scaffold | `wt/ui-scaffold` | #10 |
| CLI scaffold | `wt/cli-scaffold` | #11 |
| Windows GBK encoding hotfix (direct) | — | `807cd74` |

Wave-1 worktrees and branches were removed after merge; the briefs below are kept for reference. Wave 2 starts in the next section.

---

## Wave 2 — now (5 parallel worktrees)

| Worktree | Branch | Path |
|---|---|---|
| Pipeline detection | `wt/dataflow` | `D:/AIcoding/aitap-dataflow` |
| Local persistence | `wt/store` | `D:/AIcoding/aitap-store` |
| Remote audit | `wt/audit` | `D:/AIcoding/aitap-audit` |
| Provider clients | `wt/providers` | `D:/AIcoding/aitap-providers` |
| L2 deep scanner | `wt/deep-scan` | `D:/AIcoding/aitap-deep-scan` |

**Coordination notes for Wave 2**

- `scanner/engine.py` will be touched by both `wt/dataflow` (pipelines) and `wt/deep-scan` (L2 enrichment). Each worktree owns a clearly separated function/region. Rebase before merging the second one.
- `scanner/__init__.py` (`scan_command`) will be touched by `wt/store` (persistence hook) and `wt/deep-scan` (`--deep` flag wiring). Same advice.
- `cli.py:audit_command` body is owned by `wt/audit` (replaces the stub). No conflict expected.
- All four contract files in `CONTRACTS.md` should remain untouched. If a worktree thinks it needs a contract change, stop and follow the change protocol.

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

## wt/dataflow — Pipeline detection (Wave 2)

**Goal**: Make `aitap scan` produce non-empty `Pipeline[]` whenever a project has chained LLM calls. Covers ~70% of real-world chains (the "方案 A" scope from the plan); agent / cross-class / cross-file state stays for v0.2.

**In scope**

- `src/aitap/scanner/dataflow/base.py` — shared `DataflowDetector` Protocol with `detect(module: ast.Module, prompts: list[PromptSite]) -> list[PipelineEdge]`
- `src/aitap/scanner/dataflow/variable_tracker.py` — direct variable-flow detector. Walks the AST tracking `x = call_a(...)` then `call_b(x, ...)` patterns where both calls are known prompt sites.
- `src/aitap/scanner/dataflow/langchain_pipe.py` — recognise `prompt | model | parser | ...` BinOp chains involving identifiable LangChain primitives.
- `src/aitap/scanner/dataflow/llamaindex_engine.py` — recognise `query_engine = index.as_query_engine(...)` style construction; emit edges from retriever → synthesiser when both are LLM-touching.
- `src/aitap/scanner/dataflow/intra_file_chain.py` — same-file function call chains: `g(f(x))` where both `f` and `g` are functions that contain prompt sites.
- `src/aitap/scanner/dataflow/__init__.py` — orchestrator: runs all detectors, dedupes edges, builds `Pipeline` objects (compute `entry_points` / `exit_points` from edge set).
- Wire the orchestrator into `src/aitap/scanner/engine.py` so the resulting `ScanResult.pipelines` is populated alongside `ScanResult.prompts`. Keep this region clearly separated from L2 hooks (which `wt/deep-scan` will add).
- `tests/fixtures/langchain_rag/` — populate this empty fixture with a 3-stage chain (retriever prompt → synthesizer prompt → critic prompt) using `prompt | model | parser`.
- New fixture `tests/fixtures/var_chain/` — two prompts where the first's output is variable-fed into the second.
- Unit tests for each detector (`tests/unit/test_dataflow_*.py`) using small inline AST snippets.
- Integration test: scan `langchain_rag` fixture → assert ≥1 Pipeline with ≥2 nodes and ≥1 edge.

**Out of scope** — do NOT touch

- `src/aitap/scanner/models.py` — frozen contract; `Pipeline`/`PipelineNode`/`PipelineEdge`/`EdgeKind` already exist
- L2 enrichment (cross-file / cross-class / agent loops) — that's `wt/deep-scan` (and most of it is v0.2 anyway)
- `src/aitap/store/`, `src/aitap/deep/`, `src/aitap/cli.py`, `src/aitap/server/`, `src/aitap/ui/`
- The CLI `--deep` flag — it exists; you don't wire its body

**Acceptance criteria**

- `uv run aitap scan tests/fixtures/langchain_rag --json` returns JSON with `pipelines: [...]` non-empty
- `pytest tests/unit/test_dataflow_*.py` green; coverage ≥80% on `dataflow/`
- pyright strict clean on `dataflow/` and the touched lines of `engine.py`
- Edges with low confidence (anything beyond direct variable / langchain `|` / llamaindex / same-file functions) are emitted with `EdgeKind.UNRESOLVED` + `confidence=Confidence.LOW` so the UI can render them dashed

**Claude prompt** (paste verbatim):

```
我现在在 wt/dataflow 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/dataflow" 节的 In scope / Out of scope / Acceptance criteria 实现 Pipeline 检测的 4 个 L1 检测器。

开始前请读：
1. CONTRACTS.md
2. src/aitap/scanner/models.py（已存在的 Pipeline / PipelineNode / PipelineEdge / EdgeKind 是你输出的形状，不要改）
3. src/aitap/scanner/engine.py（你要扩它，但只在清晰的新区域加 hook，不要重构旧代码）
4. tests/fixtures/openai_basic（参考已有 fixture 结构）
5. C:\Users\1\.claude\plans\llm-humming-pike.md 中 Pipeline 链路检测一节

实现要求：
- 每个检测器一个文件 + 一个单测文件
- 不确信的边走 EdgeKind.UNRESOLVED + Confidence.LOW
- 不动 Out of scope 列出的任何文件
- 完成后单 commit + 开 PR；commit message 用 conventional commits 风格
- 如果发现 scanner/models.py 缺字段需要新增，停下来回主仓库走 CONTRACTS.md 流程
```

---

## wt/store — Local persistence (Wave 2)

**Goal**: Wave 1 scans run pure in-memory and disappear. Wave 2 makes scan results, prompts, pipelines and (forthcoming) runs survive between invocations, in `.aitap/`. This unblocks history, diff, rollback, and the Web playground.

**In scope**

- `src/aitap/store/db.py` — DAO layer on top of the contract schema (already defined). Functions: `upsert_prompt(conn, site)`, `upsert_pipeline(conn, pipeline)`, `record_provider_evidence(conn, root, ev)`, `read_prompts(conn, *, name=None)`, `read_pipelines(conn)`, etc. Use the `transaction()` helper. Don't touch the DDL.
- `src/aitap/store/files.py` — read/write the git-tracked artifacts:
  - `.aitap/prompts/<name>.prompt.yaml` — one file per `PromptSite`, deterministically formatted (so diffs are reviewable)
  - `.aitap/pipelines/<name>.pipeline.yaml` — one file per `Pipeline`
  - `.aitap/datasets/<name>.cases.jsonl` — append/load (skeleton only; actual generation is M4)
- `src/aitap/store/git_link.py` — detect whether `project_root` is a git repo (via `gitpython`); return current commit SHA, dirty/clean state. No-op when not a repo.
- `src/aitap/store/__init__.py` — `persist_scan_result(settings, result)` orchestrator that takes a `ScanResult` and writes everything: SQLite rows + YAML files + commit SHA stamping.
- Hook into `src/aitap/scanner/__init__.py:scan_command` so that after a successful scan in a project with `.aitap/` initialised, results persist. If `.aitap/` does not exist, persist is a no-op (don't auto-init).
- `tests/unit/test_store_db.py`, `test_store_files.py`, `test_store_git_link.py`, plus an integration test that runs `aitap scan` in a temp project (with `aitap init` first) and asserts the SQLite + YAML state.

**Out of scope** — do NOT touch

- `src/aitap/store/db.py` schema (DDL) — frozen contract
- History / diff / rollback (M4 scope; `store/history.py` belongs to a Wave 3+ worktree)
- Anything under `scanner/`, `deep/`, `server/`, `ui/`
- The `aitap init` command — already implemented

**Acceptance criteria**

- After `aitap init && aitap scan .` in a fixture, `.aitap/db.sqlite` has rows in `prompts` + `pipelines` + `providers_detected`
- `.aitap/prompts/<name>.prompt.yaml` files materialise; round-trip through `files.read_prompt()` reproduces the `PromptSite` exactly
- Git commit SHA recorded on the `runs`-related rows when the project is a git repo
- All new tests green; pyright strict clean
- Re-running `aitap scan` is idempotent — no duplicate rows; `last_seen_at` updates

**Claude prompt** (paste verbatim):

```
我现在在 wt/store 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/store" 节的 In scope / Out of scope / Acceptance criteria 实现持久化层。

开始前请读：
1. CONTRACTS.md
2. src/aitap/store/db.py（DDL 是契约，不要改；只在它之上加 DAO 函数）
3. src/aitap/scanner/models.py（你序列化的对象）
4. src/aitap/config.py（Settings 提供路径）
5. C:\Users\1\.claude\plans\llm-humming-pike.md 中".aitap/ 持久化"和"用户目录布局"一节

实现要求：
- YAML 输出要稳定（key 顺序固定、no flow style for nested），方便 git diff
- 不创建 .aitap/ —— 那是 aitap init 的职责；持久化层只在已有 .aitap/ 时落盘
- 重复扫描必须是幂等：用 PromptSite.id / Pipeline.id 作 PK
- 不动 Out of scope 列出的文件
- 完成后单 commit + 开 PR
```

---

## wt/audit — Remote audit mode (Wave 2)

**Goal**: Replace the `aitap audit` stub with a real implementation that clones a remote repo, runs L1 scan, prints a report, and cleans up — never touching the user's `.aitap/` and never invoking L2.

**In scope**

- `src/aitap/audit/clone.py` — `audit_repo(repo: str, *, rules_only: bool = True, keep_clone: bool = False) -> int`. Resolves `gh:owner/repo` shorthand to `https://github.com/owner/repo.git`; clones to `tempfile.mkdtemp()` (or `<project_root>/.aitap/audit-cache/` if `--keep-clone`); runs the existing scanner against the clone; renders the same Markdown report as `aitap scan`; deletes the temp dir on exit.
- Wire into `src/aitap/cli.py:audit_command` — replace the stub body. Keep the existing flag surface (`repo`, `--rules-only`, `--keep-clone`).
- Use `gitpython` (already in deps) for the clone; never run `git` as a subprocess.
- `tests/unit/test_audit_clone.py` — mock `git.Repo.clone_from` to avoid network; verify URL resolution, temp-dir lifecycle, force-rules-only behaviour.
- One `@pytest.mark.integration` end-to-end test that actually clones a small fixed repo (e.g., a sub-1MB historical snapshot we control) — skipped by default; runs only when `AITAP_RUN_INTEGRATION=1` is set.

**Out of scope** — do NOT touch

- The scanner internals — call `scan_project()` and the existing report renderer; don't reimplement
- Persistence (`store/`) — audit writes nothing to disk under `.aitap/`
- L2 enablement — even if `--rules-only=False` is passed, audit MUST refuse to run L2 against unknown code (raise typer.BadParameter)

**Acceptance criteria**

- `aitap audit gh:simonw/llm` (with `AITAP_RUN_INTEGRATION=1`) clones, scans, prints report, exits 0, leaves no temp dir
- `aitap audit invalid:not-a-repo` exits non-zero with a helpful message
- `aitap audit gh:foo/bar --keep-clone` keeps the clone at a documented path
- All non-integration tests pass without touching the network

**Claude prompt** (paste verbatim):

```
我现在在 wt/audit 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/audit" 节实现远程审计模式。

开始前请读：
1. src/aitap/cli.py 中 audit_command 现有的 stub
2. src/aitap/scanner/__init__.py（scan_project / scan_command 是你要复用的）
3. src/aitap/scanner/report.py（render_terminal_report 拿来直接用）
4. C:\Users\1\.claude\plans\llm-humming-pike.md 中 audit 模式一节

实现要求：
- 用 gitpython，不要 subprocess.run('git ...')
- 网络相关测试用 mock；真实克隆放 @pytest.mark.integration
- audit 模式严格拒绝 L2，即使 --rules-only=False 也不行
- 完成后单 commit + 开 PR
```

---

## wt/providers — LLM provider clients (Wave 2)

**Goal**: Concrete `LLMClient` implementations for Anthropic and OpenAI, registered into the `deep/client.py` registry so `get_client("anthropic", ...)` returns a working object. Cost estimation must work without making network calls.

**In scope**

- `src/aitap/deep/anthropic_client.py` — `AnthropicClient(LLMClient)` using the `anthropic` SDK. Implement `chat()` (async), `estimate_cost()` (uses a hard-coded pricing table keyed by model). Calls `register_provider("anthropic", ...)` at module bottom.
- `src/aitap/deep/openai_client.py` — `OpenAIClient(LLMClient)` similar shape using `openai` SDK. Calls `register_provider("openai", ...)` at module bottom.
- `src/aitap/deep/pricing.py` — pricing tables keyed by `(provider, model)` → `(input_per_1k_usd, output_per_1k_usd)`. Document the source URL + last-updated date so future updates are traceable. Cover Claude Sonnet 4.6, Haiku 4.5, Opus 4.7; gpt-4o, gpt-4o-mini, o1-mini.
- Token counting: use the SDK's official tokeniser when available; otherwise fall back to a "4 chars per token" heuristic with a logged warning.
- Wrap SDK errors into `ProviderAuthError` / `ProviderRateLimitError` / `ProviderError` per the contract.
- Lazy-import the SDKs inside the module body (not at top level) so `aitap` without the optional extras still imports cleanly.
- `tests/unit/test_anthropic_client.py`, `test_openai_client.py` — use SDK mocks (e.g., `respx` for httpx, or monkeypatch the SDK class) to verify request shaping, response parsing, error wrapping, cost estimation. No network.
- `@pytest.mark.integration` smoke tests gated on `AITAP_TEST_PROVIDER=anthropic` (and `=openai`) env that hit real APIs with a 5-token prompt.

**Out of scope** — do NOT touch

- `src/aitap/deep/client.py` — frozen contract (LLMClient ABC, registry)
- L2 deep-scan logic — that's `wt/deep-scan`
- The CLI surface

**Acceptance criteria**

- `from aitap.deep.client import get_client; c = get_client("anthropic", "claude-sonnet-4-6")` returns a valid `LLMClient` (no network)
- `c.estimate_cost([ChatMessage(role="user", content="hi")])` returns a `CostEstimate` with sensible numbers
- Awaiting `c.chat(messages)` against a mocked SDK returns a populated `ChatResponse` with `cost_usd` calculated from `usage`
- `import aitap` works in a fresh venv that does NOT have `anthropic` or `openai` installed (lazy import sanity check)
- pyright strict clean on `deep/`

**Claude prompt** (paste verbatim):

```
我现在在 wt/providers 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/providers" 节实现 Anthropic + OpenAI 的 LLMClient 子类。

开始前请读：
1. src/aitap/deep/client.py（LLMClient 抽象 + registry，是契约不要改）
2. CONTRACTS.md
3. anthropic / openai 各自 SDK 的最新文档（我们要 SDK 0.25+ 的 messages.create 和 1.30+ 的 chat.completions.create）

实现要求：
- 每家 provider 一个文件，两家互不依赖
- SDK 必须 lazy import（在函数体内 import，不在文件顶层）—— 因为 anthropic / openai 是 optional extras
- 错误必须包成 ProviderError 子类
- pricing.py 把价格表集中管理；改价格只改一个地方
- 测试用 SDK mock 不打网络；真实调用走 @pytest.mark.integration
- 完成后单 commit + 开 PR
```

---

## wt/deep-scan — L2 deep scanner (Wave 2)

**Goal**: When the user runs `aitap scan --deep`, three LLM-assisted enrichers fire: confirm suspicious wrappers, resolve cross-file prompt assembly, and infer prompt purpose. Always shows a cost estimate before spending money.

**In scope**

- `src/aitap/deep/wrapper_detector.py` — given a function flagged as `confidence=MEDIUM/LOW` by L1, ask the LLM whether it's a real LLM wrapper. Promote to HIGH or downgrade to "definitely not LLM" with rationale.
- `src/aitap/deep/cross_file_resolver.py` — given a prompt with `template_kind=UNRESOLVED` and the surrounding files, ask the LLM to reconstruct the full template body when possible.
- `src/aitap/deep/purpose_inferer.py` — given a `PromptSite` plus its caller context, fill `PromptSite.purpose` with a one-line description. Used downstream by `dataset/llm_expander` to generate semantically-fitting test inputs.
- `src/aitap/deep/orchestrator.py` — `enrich_with_l2(result: ScanResult, client: LLMClient) -> ScanResult`. Computes a `CostEstimate` for the full enrichment pass, calls back to a confirmation hook (passed in by the CLI), then runs the three enrichers in parallel via `asyncio.gather`. Sets `result.l2_used = True`.
- `src/aitap/deep/prompts/` — keep our own L2 system prompts as `.md` files loaded at runtime. Names: `wrapper_detect.md`, `cross_file_resolve.md`, `purpose_infer.md`.
- Wire into `src/aitap/scanner/__init__.py:scan_command` — when `--deep` is passed, after L1 finishes, build a provider/client (using `Settings`), display the cost estimate via rich, prompt for confirmation (skipped with `--yes` flag — add it), then run `enrich_with_l2`.
- Wire into `src/aitap/scanner/engine.py` only via the new `enrich_with_l2` post-processing step — keep this region clearly separated from `wt/dataflow`'s pipeline integration.
- `tests/unit/test_deep_*.py` — drive every enricher with a `MockLLMClient` (provide a tiny in-test mock that records calls + returns scripted responses). No network in unit tests.
- One `@pytest.mark.integration` test gated on `AITAP_TEST_PROVIDER` actually hits an LLM.

**Out of scope** — do NOT touch

- `src/aitap/deep/client.py` — contract
- Provider implementations (`anthropic_client.py`, `openai_client.py`) — `wt/providers` owns them; you depend only on the abstract `LLMClient`
- Pipeline detection — `wt/dataflow`
- Persistence — `wt/store`

**Acceptance criteria**

- `aitap scan tests/fixtures/<wrapper-fixture> --deep --yes` runs without crash, prints a cost line, calls the (mocked-in-test, real-in-integration) LLM, and produces `ScanResult.l2_used=True`
- `enrich_with_l2` is callable in isolation with a `MockLLMClient` (no CLI dependency)
- pyright strict clean on `deep/`
- Cost estimate printed BEFORE any API call; confirmation required unless `--yes`
- New fixture `tests/fixtures/wrapped_llm/` with a custom function wrapping an LLM call (not detectable by L1)

**Claude prompt** (paste verbatim):

```
我现在在 wt/deep-scan 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/deep-scan" 节实现 L2 深度扫描。

开始前请读：
1. CONTRACTS.md
2. src/aitap/deep/client.py（LLMClient 抽象，是契约）
3. src/aitap/scanner/models.py（PromptSite/ScanResult 是输入输出形状，是契约）
4. src/aitap/scanner/engine.py 和 scanner/__init__.py（你要在这两处加 L2 hook）
5. C:\Users\1\.claude\plans\llm-humming-pike.md 中 L2 LLM 辅助扫描一节

实现要求：
- 单测用自己写的 MockLLMClient，不依赖 wt/providers 的真实实现
- 三个 enricher 互相独立，可以 asyncio.gather 并发
- 跑 L2 前必须显示成本预估并要确认（除非 --yes）
- L2 的 system prompts 放 deep/prompts/*.md，runtime 加载，方便日后调
- 不动 Out of scope 列出的文件
- 完成后单 commit + 开 PR
```

---

## Wave 3 — next (6 parallel worktrees, M3 Web Playground + Pipeline Runner)

| Worktree | Branch | Path |
|---|---|---|
| Prompts/history API | `wt/api-prompts` | `D:/AIcoding/aitap-api-prompts` |
| Runs/settings API | `wt/api-runs` | `D:/AIcoding/aitap-api-runs` |
| Playground runner | `wt/runner` | `D:/AIcoding/aitap-runner` |
| UI inventory pages | `wt/ui-inventory` | `D:/AIcoding/aitap-ui-inventory` |
| UI playground pages | `wt/ui-playground` | `D:/AIcoding/aitap-ui-playground` |
| Test-case generators | `wt/dataset` | `D:/AIcoding/aitap-dataset` |

**Coordination notes for Wave 3**

- `server/app.py` (FastAPI entry) will be touched by both `wt/api-prompts` and `wt/api-runs` to register their routers. Each owns a clearly separated `app.include_router(...)` line; minor merge.
- `cli.py:ui_command` body is owned by **`wt/runner`** (not the API worktrees) — its job is to launch uvicorn against the assembled FastAPI app and open the browser.
- React pages share `ui/src/router.tsx`. Both UI worktrees add their own page routes; advise minor rebase on the second-merged.
- All four contract files stay frozen. If a Wave-3 worktree thinks the OpenAPI shapes need new fields, follow the `CONTRACTS.md` change protocol — do not edit unilaterally.

---

## wt/api-prompts — Prompts/Pipelines/History API (Wave 3)

**Goal**: Implement the read-side of the FastAPI surface — list/detail endpoints for prompts and pipelines, plus the history view that the UI's diff/rollback flows depend on.

**In scope**

- `src/aitap/server/routes/prompts.py` — `GET /api/prompts`, `GET /api/prompts/{id}`, `POST /api/prompts/{id}/versions` (returns the contract response shapes)
- `src/aitap/server/routes/pipelines.py` — `GET /api/pipelines`, `GET /api/pipelines/{id}`
- `src/aitap/server/routes/history.py` — `GET /api/history/{prompt_id}`, `POST /api/history/{prompt_id}/rollback`
- `src/aitap/store/history.py` — DAO helpers: `next_version_for(prompt_id)`, `record_version(...)`, `read_versions(prompt_id)`, `diff_versions(prompt_id, v1, v2)`, `rollback_version(...)`. The `aitap diff` / `aitap rollback` CLI stubs in `cli.py` already look for this module via `find_spec`.
- Wire routers into `server/app.py` (creating the file if it doesn't exist yet) via `app.include_router(prompts_router, prefix="/api")`.
- `tests/integration/test_api_prompts.py` — use `httpx.AsyncClient(app=app)` to exercise endpoints against a temp `.aitap/` populated by a real `scan_project` run.

**Out of scope**

- Run/feedback/iteration endpoints (`wt/api-runs` owns)
- Server bootstrap / uvicorn launch (`wt/runner` owns)
- Any changes to `server/routes/__init__.py` (contract)

**Acceptance criteria**

- `httpx.AsyncClient` end-to-end: `GET /api/prompts` after a fixture scan returns `PromptListResponse` with the expected count
- `aitap diff <prompt> 1 2` no longer prints "not yet implemented" — it returns a real diff
- `aitap rollback <prompt> 1 --yes` creates a new head version pointing at v1's content
- All endpoints return contract-defined Pydantic shapes (zero hand-rolled dicts)
- pyright strict + ruff clean

**Claude prompt**:

```
我现在在 wt/api-prompts 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/api-prompts" 节实现 prompts/pipelines/history 三组 API + history DAO。

开始前请读：
1. CONTRACTS.md
2. src/aitap/server/routes/__init__.py（API 形状契约）
3. src/aitap/store/db.py（DDL，含 prompt_versions 表）
4. src/aitap/store/__init__.py（已有的 persist_scan_result 模式）
5. src/aitap/cli.py 中 diff_command / rollback_command（stub 检测的是 aitap.store.history）

实现要求：
- 路由文件用 APIRouter，全部 response_model 指向 contract 形状
- store/history.py 是 DAO 层，CLI stub 完成后自动激活
- 真实数据集成测试用 httpx.AsyncClient
- 不动契约文件
- 单 commit + 开 PR
```

---

## wt/api-runs — Runs/Settings API (Wave 3)

**Goal**: The write-side: create/list runs, attach feedback, fire iteration, and expose the settings surface (provider, cost limits, providers detected).

**In scope**

- `src/aitap/server/routes/runs.py` — `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs?target_id=...`, `POST /api/runs/{id}/feedback`, `POST /api/runs/{id}/iterate`
- `src/aitap/server/routes/settings.py` — `GET/PUT /api/settings`, `GET /api/settings/cost-estimate?prompt_id=...&model=...`
- `src/aitap/store/runs.py` — DAO for runs/scores/feedback (insert + read; the schema already exists)
- `src/aitap/iterate/__init__.py` — minimal stub `iterate_one_round(...)` that delegates to `wt/deep-scan`'s orchestrator pattern but is wired to the runs/feedback tables (full critique loop is M4 — for now a single-round runner is enough to make the iterate endpoint real)
- Wire routers into `server/app.py` (alongside `wt/api-prompts`)
- `tests/integration/test_api_runs.py` — POST a run, attach feedback, hit /iterate, assert state via the runs/scores tables

**Out of scope**

- Prompts/pipelines/history endpoints (`wt/api-prompts` owns)
- The actual run executor (`wt/runner` owns — this worktree just records what runner produces)
- LLM-as-judge / convergence loops (M4)

**Acceptance criteria**

- `POST /api/runs` accepts a `RunCreate`, queues a run, returns `RunResponse` with status
- `POST /api/runs/{id}/feedback` writes to the `feedback` table and returns the feedback id
- `GET /api/settings` reflects the current `Settings()` and detected providers
- pyright strict + ruff clean

**Claude prompt**:

```
我现在在 wt/api-runs 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/api-runs" 节实现 runs/settings 两组 API + 最小 iterate stub。

开始前请读：
1. CONTRACTS.md
2. src/aitap/server/routes/__init__.py 中 RunCreate / FeedbackCreate / IterateRequest / SettingsResponse
3. src/aitap/store/db.py（runs/scores/feedback 表）
4. src/aitap/config.py（Settings）
5. wt/runner 的 brief（你 POST /api/runs 时实际执行交给它，不要重复造）

实现要求：
- 真实 iterate 闭环留 M4；本波只要把 endpoint 跑通：拿 feedback、调 wt/runner 的 runner、写一行新 prompt_version
- 不动契约文件
- 不动 wt/api-prompts 拥有的文件
- 单 commit + 开 PR
```

---

## wt/runner — Playground runner (Wave 3)

**Goal**: Execute prompts and pipelines against a chosen provider/model; this is the engine that powers `POST /api/runs`. Single source of truth for "given a prompt + dataset cases, produce outputs".

**In scope**

- `src/aitap/playground/runner.py` — `async def run_prompt(site, version, dataset_cases, client, parameters) -> list[RunOutput]`. One LLM call per case via `asyncio.gather`. Records token usage + cost via the client's `chat()` return.
- `src/aitap/playground/pipeline_runner.py` — three modes from the plan:
  - `node`: run a single PromptSite inside a pipeline (delegates to `runner.run_prompt`)
  - `segment`: run a contiguous slice of node ids; pipe outputs through using the dataflow edges
  - `end_to_end`: feed `cases.inputs` at entry_points, walk the DAG, capture every intermediate output to `intermediates`
- `src/aitap/server/app.py` — assemble FastAPI app + uvicorn bootstrap, mount static React assets if present. Replaces the placeholder app.py if `wt/api-prompts` created one.
- `src/aitap/cli.py:ui_command` — replace stub body with `uvicorn.run(server.app.app, ...)` + auto-open browser unless `--no-browser`.
- `tests/unit/test_playground_runner.py` — drive with `MockLLMClient`; verify per-case outputs, cost aggregation, error case captures
- `tests/unit/test_pipeline_runner.py` — three modes against a 3-node DAG fixture

**Out of scope**

- API endpoint implementation (`wt/api-runs` owns)
- Iteration / judge logic (M4)
- Image-prompt grid view (M5)

**Acceptance criteria**

- `aitap ui` actually serves a FastAPI app on the chosen port (even with no UI bundle: returns `{"status":"ok"}` from `/api/health`)
- pipeline_runner end-to-end mode produces all intermediates given an entry-point input
- pyright strict + ruff clean

**Claude prompt**:

```
我现在在 wt/runner 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/runner" 节实现 prompt/pipeline runner + server bootstrap + ui 命令体。

开始前请读：
1. CONTRACTS.md
2. src/aitap/scanner/models.py（PromptSite/Pipeline/PipelineEdge）
3. src/aitap/server/routes/__init__.py（RunOutput / DatasetCase）
4. src/aitap/deep/client.py（LLMClient 抽象）
5. src/aitap/deep/testing.py（MockLLMClient，单测用）
6. src/aitap/cli.py 中 ui_command 现有的 stub
7. wt/api-runs 的 brief（API 怎么调你）

实现要求：
- 用 asyncio.gather 并发跑 cases；不要顺序 await
- pipeline end-to-end 必须保留每个节点的中间输出（写到 intermediates）
- aitap ui 启动 uvicorn；--no-browser 控制是否开浏览器
- 不动契约文件 / wt/api-prompts / wt/api-runs / wt/dataset
- 单 commit + 开 PR
```

---

## wt/ui-inventory — Inventory pages with real API (Wave 3)

**Goal**: Replace the mock-data Inventory / PromptDetail / PipelineDetail pages with real `tanstack/react-query` calls against `/api/prompts` and `/api/pipelines`.

**In scope**

- `src/aitap/ui/src/api/generated/` — run `pnpm gen:api` against the server's OpenAPI; commit the generated TS types
- `src/aitap/ui/src/api/client.ts` — wire the generated client (replace the placeholder in Wave 1)
- `src/aitap/ui/src/pages/Inventory.tsx` — switch from `mock.ts` to `useQuery({ queryKey: ['prompts'], queryFn: fetchPrompts })`; preserve the dual-tab Prompts/Pipelines layout
- `src/aitap/ui/src/pages/PromptDetail.tsx` — fetch `/api/prompts/{id}`; render version list with diff buttons (UI only — diff modal can be a placeholder linking to `aitap diff` for now)
- `src/aitap/ui/src/pages/PipelineDetail.tsx` — fetch `/api/pipelines/{id}`; the existing `DagView` keeps working with real Pipeline data
- Loading/error states for every fetch (skeleton + retry button)
- `tests/ui/inventory.spec.ts` — Playwright/vitest E2E (skipped without API server) — at minimum, vitest component tests with msw mocking

**Out of scope**

- Playground / History pages (`wt/ui-playground` owns)
- Iteration UI (M4)
- API implementation (`wt/api-prompts` owns)

**Acceptance criteria**

- `pnpm dev` against a running `aitap ui` shows real prompts/pipelines from a scanned fixture
- `pnpm typecheck` clean
- `pnpm lint` clean
- DagView renders edges with the correct `EdgeKind` styling (variable=solid, langchain_pipe=solid, llamaindex/unresolved=dashed)

**Claude prompt**:

```
我现在在 wt/ui-inventory 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/ui-inventory" 节把 Inventory / PromptDetail / PipelineDetail 三页接上真实 API。

开始前请读：
1. src/aitap/server/routes/__init__.py（API 形状）
2. src/aitap/ui/src/api/mock.ts（要替换的对象）
3. src/aitap/ui/src/pages/{Inventory,PromptDetail,PipelineDetail}.tsx（现有 mock 版）
4. src/aitap/ui/package.json 中 gen:api 脚本

实现要求：
- 先跑 gen:api 生成 TS 客户端，然后用之
- React Query 管缓存，不要手写 fetch loops
- Loading/error 都有 UI 状态
- 不动 wt/ui-playground 的页面
- 单 commit + 开 PR
```

---

## wt/ui-playground — Playground + History pages (Wave 3)

**Goal**: The "do work" pages — pick a prompt + dataset, fire a run, watch results stream in; later visit History to see versions/scores/diff.

**In scope**

- `src/aitap/ui/src/pages/Playground.tsx` — prompt selector → dataset editor (inline JSON cases) → model/params controls → "Run" button → results table with per-case output, cost, latency
- `src/aitap/ui/src/pages/History.tsx` — version timeline + per-version score chart; diff button opens side-by-side compare modal
- `src/aitap/ui/src/components/ResultsTable.tsx` — reusable across Playground and History
- `src/aitap/ui/src/components/CaseEditor.tsx` — reusable JSON editor with validation
- Hooked to `POST /api/runs`, `GET /api/runs/{id}`, `POST /api/runs/{id}/feedback`, `GET /api/history/{prompt_id}`
- React Query optimistic updates so feedback feels instant

**Out of scope**

- Inventory / detail pages (`wt/ui-inventory` owns)
- Auto-iterate UI (M4)
- Image-prompt grid (M5)

**Acceptance criteria**

- Open Playground for a real prompt → add 3 cases → Run → results table populates
- History page shows ≥1 version per prompt that's been run
- pnpm typecheck / lint clean

**Claude prompt**:

```
我现在在 wt/ui-playground 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/ui-playground" 节实现 Playground + History 两页 + 复用组件。

开始前请读：
1. src/aitap/server/routes/__init__.py（RunCreate / RunOutput / FeedbackCreate / HistoryEntry）
2. src/aitap/ui/src/pages/{Playground,History}.tsx（现有 mock 版）
3. wt/ui-inventory 的 brief（你和它共用 API client + react-query）

实现要求：
- 反馈用 React Query 的 optimistic updates，按钮点完立刻看反应
- ResultsTable 和 CaseEditor 提到 components/ 复用
- 不动 wt/ui-inventory 的页面
- 单 commit + 开 PR
```

---

## wt/dataset — Test-case generators (Wave 3)

**Goal**: The "L0/L1/L2 四级火箭" of the plan's test-case strategy — make adding 30 cases for a prompt cheap. Powers the dataset editor in `wt/ui-playground` and the iterate loop in M4.

**In scope**

- `src/aitap/dataset/seed.py` — read/write user-provided seed cases from `.aitap/datasets/<name>.cases.jsonl` (uses `wt/store`'s `files.append_cases`)
- `src/aitap/dataset/llm_expander.py` — `async def expand(seeds, count, client, prompt_purpose) -> list[Case]`: ask the LLM to generate variants (boundary/adversarial/noise) given seeds + the prompt's purpose
- `src/aitap/dataset/code_context.py` — `infer_input_shape(site, project_root) -> InputShape`: read the call site's surrounding code (function signature, type hints) to describe what shape the input should take; used as additional grounding for `llm_expander`
- `src/aitap/dataset/fixture_miner.py` — scan the project's `tests/`, `fixtures/`, `examples/` for existing dict/JSON literals that look like prompt inputs; surface them as candidate seeds
- `src/aitap/dataset/__init__.py` — orchestrator: `generate_cases(site, mode="seed"|"expand"|"context"|"fixtures", n=10, client=None) -> list[Case]`
- All tests use `MockLLMClient` from `wt/deep-scan` (no network)

**Out of scope**

- Iteration / scoring (M4)
- UI for editing cases (`wt/ui-playground` handles the UX)

**Acceptance criteria**

- `expand([{...}, {...}], count=5, client=MockLLMClient(scripted=...))` returns 5 cases with the LLM's responses parsed
- `infer_input_shape` against the openai_basic fixture returns a non-empty InputShape (e.g., `{"text": "string", "topic": "string"}`)
- `fixture_miner` finds at least one candidate in `tests/fixtures/openai_basic`
- pyright strict + ruff clean

**Claude prompt**:

```
我现在在 wt/dataset 分支上的 worktree 里。

请按 WORKTREES.md 中 "wt/dataset" 节实现测试用例生成的 L0/L1/L2 三层（L3 推到 v0.2）。

开始前请读：
1. CONTRACTS.md
2. src/aitap/scanner/models.py（PromptSite，特别是 location + parameters + purpose）
3. src/aitap/store/files.py（append_cases / read_cases）
4. src/aitap/deep/client.py + src/aitap/deep/testing.py（MockLLMClient）
5. C:\Users\1\.claude\plans\llm-humming-pike.md 中"测试用例策略"

实现要求：
- llm_expander 的 LLM 调用走 LLMClient 抽象，不直接打 SDK
- code_context.py 用 ast 模块；不要发明新 AST 工具，能复用 scanner.languages.python 的就复用
- fixture_miner 扫 tests/fixtures examples 里的字面量，启发式提取
- 全部测试用 MockLLMClient
- 单 commit + 开 PR
```

---

## Coordination

**Daily**: each worktree rebases on `origin/main` first thing.

**Contract changes**: see `CONTRACTS.md` — single PR, broadcast to all worktrees, everyone rebases.

**Wave 2 → Wave 3 sync**: when all 5 Wave-2 worktrees merge, tag `wave-2-complete` on main and proceed to Wave 3 (see plan).

**Wave 3 → Wave 4 sync**: when all 6 Wave-3 worktrees merge, tag `wave-3-complete` and proceed to Wave 4 (M4: iteration / judge / impact analysis).
