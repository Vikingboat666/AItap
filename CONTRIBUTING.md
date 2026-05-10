# Contributing to aitap

Thanks for considering a contribution! This guide is short on purpose —
the goal is to get you from "I see a thing to improve" to "PR open" with
minimum friction.

## Ground rules

- Be excellent to each other — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Bug reports and scanner false-positive reports are first-class
  contributions. See the
  [issue templates](.github/ISSUE_TEMPLATE/).
- Every accepted false-positive becomes a regression fixture.

## Project shape

```
src/aitap/         # the package
tests/             # unit + integration tests, with shared fixtures
docs/              # mkdocs-material site
examples/starter/  # runnable example used to dogfood `aitap scan`
.github/workflows/ # CI + release
```

The four files in [CONTRACTS.md](CONTRACTS.md) are shared interfaces.
Changes there are coordinated separately — see that document.

## Local setup

aitap uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# 1. install uv: https://docs.astral.sh/uv/getting-started/installation/

# 2. clone and sync dev tooling
git clone https://github.com/aitap/aitap.git
cd aitap
uv sync --group dev --group docs

# 3. install pre-commit hooks
uv run pre-commit install
```

If you don't have `make` (e.g. on Windows without WSL), every Make target
maps to a one-liner — see the [Makefile](Makefile).

## Common tasks

```bash
make install     # uv sync --group dev --group docs
make lint        # ruff check + ruff format --check
make format      # ruff format + ruff check --fix
make test        # pytest
make docs        # mkdocs serve at http://127.0.0.1:8000
make build       # uv build (sdist + wheel)
```

## Testing philosophy

- Unit tests live under `tests/unit/` and run on every push.
- Integration tests live under `tests/integration/`. They may be slower
  but must not require network access or real API keys — use the mocks
  in `examples/starter/starter_app/mocks.py` or write a fake client in
  the test.
- New scanner rules **must** ship with a fixture project under
  `tests/fixtures/<scenario>/` and a unit test that asserts the expected
  `PromptSite` shape.

## Adding a scanner rule

1. Add a fixture project under `tests/fixtures/<scenario_name>/` —
   minimum viable code that reproduces the call shape you want to match.
2. Extend `src/aitap/scanner/rules/sdk_calls.py` (or write a sibling
   module).
3. Add a unit test in `tests/unit/` asserting the rule produces the
   expected `PromptSite`.
4. If your rule changes anything in `src/aitap/scanner/models.py`,
   **stop** — that's a contract change. Open a separate PR per
   [CONTRACTS.md](CONTRACTS.md).

## Commit style

We follow [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <imperative summary>

<optional body, wrapped at 100 cols>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`,
`build`, `perf`. Scope is optional — common scopes are `scanner`,
`store`, `ui`, `cli`, `docs`, `infra`.

Example:

```
feat(scanner): detect openai responses.create call shape

Adds the rule and a fixture under tests/fixtures/openai_responses/.
Falls back to medium-confidence when `instructions` is a non-literal.
```

## Pull request checklist

- [ ] Rebased on `origin/main` (no merge commits)
- [ ] `make lint` clean
- [ ] `uv run pyright` shows no new errors
- [ ] `make test` green locally
- [ ] User-visible behavior changes are reflected in `docs/`
- [ ] Conventional-commit-style commit messages

CI runs the same checks on Linux / macOS / Windows × Python 3.10 / 3.11 /
3.12. A green CI is required before merge.

## License

By contributing, you agree that your contributions will be licensed under
the [Apache 2.0 License](LICENSE).
