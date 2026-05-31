<!--
Thanks for contributing to aitap!

Before submitting:
- Read CONTRIBUTING.md if you haven't.
- If this PR touches a contract file (see CONTRACTS.md), it should be a single
  contract-only PR — please don't bundle contract changes with feature work.
-->

## Summary

<!-- One or two sentences. What does this PR change and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / internal cleanup
- [ ] Docs only
- [ ] CI / tooling
- [ ] Contract change (CONTRACTS.md) — coordinate with owners

## Worktree / wave

<!-- If this PR comes from a parallel worktree, name the branch (e.g. wt/scanner-core). -->

## Checklist

Per `CLAUDE.md` regulations — items marked 🤖 are enforced by
`tests/unit/test_doc_currency.py` in CI:

- [ ] 🤖 **CHANGELOG.md** `[Unreleased]` references this PR by `#NNN` under the right subsection (`Added` / `Changed` / `Fixed` / `Quality` / `Coming in 0.X.Y`). Skip with `[no-changelog]` in the merge commit message for genuinely trivial PRs (typo fix, comment-only cleanup).
- [ ] 🤖 **Design doc Status:** line stays current. If this PR ships a worktree mentioned in a `docs/*-design.md`, update the doc's `Status:` line in the same commit (e.g. `Approved` → `Implemented in PR #NNN (YYYY-MM-DD)`).
- [ ] **WORKTREES.md** active roadmap table reflects this PR's effect (worktree status moved from ⏳ to ✅, or a new worktree appears).
- [ ] **i18n** — every new user-facing UI string is added to BOTH `src/aitap/ui/src/i18n/en.json` and `zh.json`. The `i18n.parity.test.ts` test catches drift.
- [ ] **Plain-language copy** — UI text, CLI prints, `HTTPException(detail=…)`, errors, banners, empty states name the next action and don't surface raw status codes or stack traces.
- [ ] Four gates green: `uv run pyright src/aitap`, `uv run ruff check src/aitap tests`, `uv run pytest tests/ -q`, and (if frontend touched) `pnpm typecheck && pnpm lint && pnpm test && pnpm build`.
- [ ] Added or updated tests for the change.
- [ ] `make lint` clean (or `uv run ruff check . && uv run ruff format --check .`)
- [ ] `make test` green (or `uv run pytest`)
- [ ] `uv run pyright` has no new errors

## Notes for reviewers

<!-- Anything specific you want a second pair of eyes on. -->
