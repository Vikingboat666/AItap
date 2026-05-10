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

- [ ] `make lint` clean (or `uv run ruff check . && uv run ruff format --check .`)
- [ ] `make test` green (or `uv run pytest`)
- [ ] `uv run pyright` has no new errors
- [ ] Updated docs under `docs/` if user-visible behavior changed
- [ ] Added or updated tests for the change

## Notes for reviewers

<!-- Anything specific you want a second pair of eyes on. -->
