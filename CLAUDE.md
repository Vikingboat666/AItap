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

## Project structure / conventions

See `CONTRACTS.md` for the cross-module contracts (frozen files), `CONTRIBUTING.md` for workflow, and `WORKTREES.md` for the parallel-worktree development pattern.
