# aitap starter example

A small, runnable Python project that demonstrates the kinds of LLM calls
`aitap scan` is designed to find. It uses both the OpenAI and Anthropic
SDK call shapes, organized as a tiny two-step pipeline (summarize → critique).

This example is **fully runnable without any API keys** — the entry point
swaps in a `FakeClient` that mimics the SDK surface and returns canned
responses, so you can dogfood `aitap` against realistic-looking source
code immediately.

## Run it

From the repo root:

```bash
python examples/starter/main.py
```

You'll see the mocked summarize → critique flow execute end-to-end.

## Scan it

From the repo root:

```bash
aitap scan examples/starter
```

You should see at least two `PromptSite`s detected — the OpenAI summarizer
and the Anthropic critic — plus the inferred two-node pipeline.

## What's in here

```
examples/starter/
├── README.md
├── main.py                    # Entry point — wires FakeClient by default
└── starter_app/
    ├── __init__.py
    ├── openai_summarizer.py   # Uses openai SDK call shape
    ├── anthropic_critic.py    # Uses anthropic SDK call shape
    └── mocks.py               # FakeOpenAI / FakeAnthropic for offline runs
```

The repo-level smoke test for this example lives at
`tests/integration/test_starter_example.py` and runs as part of
`make test`.

## Using real providers

If you actually want to hit the live APIs (this example was scanned by
`aitap` to extract the call sites — running it for real is just a
demonstration), install the relevant extra and pass a real client into
`run_pipeline()`:

```bash
pip install "openai>=1.30" "anthropic>=0.25"
```

```python
from openai import OpenAI
from anthropic import Anthropic
from starter_app.openai_summarizer import summarize_email
from starter_app.anthropic_critic import critique_summary

summary = summarize_email(OpenAI(), email_body)
critique = critique_summary(Anthropic(), summary)
```
