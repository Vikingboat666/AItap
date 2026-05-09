# aitap

> One-tap discovery and iteration for prompts in your AI codebase.

`aitap` ("AI Tap") is a zero-config CLI that scans any LLM-powered project, extracts every prompt and pipeline, and gives you a local Web Playground to test, iterate, and version them — before they ship.

## Why aitap

Prompts in real codebases are scattered across f-strings, templates, and config files. Today's debugging loop is reactive: you ship, users find regressions, you patch. Existing tools either require heavy yaml setup (Promptfoo), framework lock-in (DSPy), or live in production (LangSmith). Nobody solves the first mile: **"I don't even know all the prompts I have."**

`aitap` fixes that.

## What it does (v0.1)

- **Auto-discover** prompts and pipelines (DAG of LLM calls) in any Python project — zero config
- **Web Playground** for batch-running, comparing versions, and human-in-the-loop iteration
- **Self-iteration loop** powered by combined feedback (👍/👎, ideal answers, rules, LLM-as-judge)
- **Pipeline-aware**: detects RAG / agent / multi-step chains; warns about downstream impact when iterating a single node
- **Local-first**: all data stays on your machine; reuses your project's existing API keys
- **Audit mode**: `aitap audit gh:owner/repo` to safely explore any open-source AI project

## Status

Pre-alpha. Active development.

## License

Apache 2.0
