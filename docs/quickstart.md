# Quickstart

This guide gets you from "fresh checkout" to "scanning a real project" in
about three minutes. aitap is **local-first** — nothing leaves your machine.

## Install

`aitap` runs on Python 3.10 or newer. Pick whichever flow you prefer:

=== "uvx (zero install)"

    ```bash
    uvx aitap --help
    ```

=== "pipx (isolated install)"

    ```bash
    pipx install aitap
    aitap --help
    ```

=== "pip"

    ```bash
    pip install aitap
    aitap --help
    ```

## First scan

`aitap` defaults to the L1 rule-based scanner — fast, deterministic, no API
calls.

```bash
cd path/to/your/python/project
aitap scan
```

You'll get a Markdown report listing every detected prompt, the file/line
where it lives, and the SDK signature it matched.

To force purely rule-based scanning (CI-friendly, no LLM cost):

```bash
aitap scan --rules-only
```

To enable LLM-assisted scanning that uses your project's existing API keys
to disambiguate wrappers and infer prompt purpose:

```bash
aitap scan --deep
```

`aitap scan --deep` always shows a token / cost estimate and asks for
confirmation before making any LLM calls.

## Try it on the bundled example

The `examples/starter/` project ships with a tiny app that calls a
couple of LLM endpoints — scan it to see the report shape:

```bash
git clone https://github.com/aitap/aitap.git
cd aitap/examples/starter
aitap scan
```

## Initialize persistent storage

Once you want to keep history across runs:

```bash
aitap init
```

This creates a `.aitap/` directory in the current project and adds the
ephemeral pieces to `.gitignore`. The layout is:

```
.aitap/
├── config.yaml     # profile list + defaults + cost caps
├── db.sqlite       # gitignored — your local run history
├── prompts/        # git-tracked — extracted prompts
├── pipelines/      # git-tracked — extracted DAGs
├── datasets/       # git-tracked — test cases
└── runs/           # gitignored — per-run snapshots
```

## Open the Web Playground

```bash
aitap ui
```

This serves the playground at `http://localhost:7860` — pick a prompt or
pipeline, attach test cases, run a batch, and iterate.

## Audit any open-source project

```bash
aitap audit gh:simonw/llm
```

This clones the repo into a temp directory, runs an L1 scan, prints the
report, and cleans up. No writes, no persistence — handy for sizing up an
unfamiliar codebase.

## Where to next

- [Architecture](architecture.md) — how the scanner, store, and runner fit
  together.
- [Rules overview](rules/index.md) — what the L1 scanner currently catches.
- [Contributing](https://github.com/aitap/aitap/blob/main/CONTRIBUTING.md)
  — how to file a bug, add a rule, or open a PR.
