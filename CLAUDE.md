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

## Project structure / conventions

See `CONTRACTS.md` for the cross-module contracts (frozen files), `CONTRIBUTING.md` for workflow, and `WORKTREES.md` for the parallel-worktree development pattern.
