# Working on `aitap` with Claude Code

Project-level instructions for any Claude Code session in this repo.

## Privacy guardrails — non-negotiable

This is a **public repository**. Never write the following into tracked files (commit message, code, docs, fixtures, tests, notebooks):

- **Absolute local paths** — `C:\Users\...`, `D:\...`, `/home/<user>/...`, `/Users/<user>/...`. Use relative paths, `~`, or placeholders like `<workspace>/...` or `$PROJECT_ROOT/...`.
- **Personal identifiers** beyond the GitHub committer metadata that already shows up in `git log` (i.e. don't *add* new mentions of full names, real emails, phone numbers, addresses).
- **Internal tool paths** — `.claude/plans/...`, IDE workspace files, local plan/scratch docs that live outside the repo.
- **Real secrets** of any kind — even commented out, even in markdown blocks marked as examples. Use the documented placeholders (`sk-replace-me`, `<your-token>`, `${OPENAI_API_KEY}`).

A `pygrep` pre-commit hook (`no-hardcoded-local-paths` in `.pre-commit-config.yaml`) catches the path cases. Don't bypass it without checking with the maintainer.

## When in doubt about whether something is publishable

Ask the user before committing it. The repo is public; reverting a leak after push requires `git filter-repo` and breaks every existing PR/commit URL.

## Internationalization (i18n) — non-negotiable

The Web UI ships in **both English and 简体中文**. Every future UI change must keep both in sync — there is no English-only or Chinese-only feature.

- **No hardcoded user-facing strings** in `src/aitap/ui`. Every label, button, placeholder, title, empty-state, error/warning text, and `aria-label` goes through `react-i18next` (`const { t } = useTranslation()` → `t("area.key")`).
- **Add every new key to BOTH** `src/aitap/ui/src/i18n/en.json` and `src/aitap/ui/src/i18n/zh.json`. The `i18n.parity.test.ts` test fails CI if the two locales' key sets differ or any value is blank.
- **Do NOT translate** identifiers/data: prompt ids, provider/model names, file paths, wire enums (`node`/`segment`/`end_to_end`), version numbers, or API-returned values. Only static UI prose.
- Use named interpolation (`t("k", { count })`) — never string concatenation that breaks translation.
- Chinese should read naturally and keep technical terms (prompt, pipeline, token, …) consistent.

## Plain-language UI copy — non-negotiable

Every user-facing string — UI text, CLI output, error messages, empty states, banners, tooltips — must be **直白易懂 / plain language**. The reader is a working developer, not a compiler.

- **Use everyday words.** Prefer "No API key configured" over "Provider authentication is unconfigured". Prefer "Couldn't connect" over "Network transport unavailable".
- **Tell them what to do next, not just what failed.** Bad: `401 Unauthorized`. Good: `Anthropic rejected the key — open Settings to fix it.` Empty states explain the cause + the next action: `No prompts found. Try `aitap scan` in your project root.`
- **Active voice + short sentences.** One thought per line. Prefer "Aitap couldn't reach OpenAI" over "Connection to OpenAI could not be established".
- **Never surface raw stack traces, status codes, or internal names** to end users. Map them to a human sentence; keep the technical detail in the expandable "details" / `pre` block.
- **Same rule in 中文**: 用日常说法、避开术语堆砌、给出下一步动作。技术词（prompt / pipeline / token / 模板）必要时保留，但首次出现给一两句解释。en/zh 两版都要满足这条标准 —— 不是只英文要直白。
- Applies to: `i18n/en.json` + `zh.json`, all `typer` CLI prints, `HTTPException(detail=...)` strings surfaced to the UI, exception messages that bubble up to the user, button labels, placeholders, tooltips, banners, toasts.
- Code reviewers should call out copy that reads like an internal log line or a stack trace.

## Project structure / conventions

See `CONTRACTS.md` for the cross-module contracts (frozen files), `CONTRIBUTING.md` for workflow, and `WORKTREES.md` for the parallel-worktree development pattern.
