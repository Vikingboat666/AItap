# Rules overview

aitap's L1 scanner is a collection of **rules** that pattern-match known
LLM call signatures and extract structured information. This page is a
living catalog of what we currently catch.

## What a rule does

Each rule:

1. Recognizes a known SDK call shape (a particular function on a particular
   class, with particular keyword arguments).
2. Extracts the prompt content (string literal, f-string, multi-line
   string, basic Jinja2 template).
3. Extracts call metadata — model, temperature, max_tokens, message roles.
4. Returns a `PromptSite` pinned to a `(file, line, col)` location.

## Currently supported (v0.1)

| Provider / library | Call shape | Status |
|---|---|---|
| **OpenAI** (`openai>=1.0`) | `client.chat.completions.create(...)` | ✅ |
| **OpenAI** (`openai>=1.0`) | `client.responses.create(...)` | planned |
| **Anthropic** (`anthropic>=0.25`) | `client.messages.create(...)` | ✅ |
| LangChain | `ChatOpenAI / ChatAnthropic` instantiation + `.invoke(...)` | partial |
| LangChain pipelines | `prompt | model | parser` operator chains | planned (M2) |
| LlamaIndex | `query_engine.query(...)` chains | planned (M2) |
| Custom wrappers | any user-defined function that ultimately hits an SDK | L2 only |

Rules ship in `src/aitap/scanner/rules/sdk_calls.py`. Adding a rule is
the most common kind of contribution — see
[CONTRIBUTING](https://github.com/aitap/aitap/blob/main/CONTRIBUTING.md)
for the workflow.

## Confidence levels

- **High confidence** — known SDK signature, fully resolvable arguments
  (string literal or f-string with locally-bound variables).
- **Medium confidence** — known signature but at least one argument is a
  reference to something the L1 scanner can't resolve (cross-file, runtime
  config, etc.). Surfaced with a "needs L2" annotation.
- **Low confidence / template-not-parsed** — Jinja2 or f-string with
  complex interpolation. Reported as a site, but prompt content is left as
  the raw template plus a parser warning.

## Reporting a missed prompt

If aitap missed a real prompt or flagged a non-prompt, please file a
[scanner false-positive issue](https://github.com/aitap/aitap/issues/new?template=scanner_false_positive.yml)
with the smallest snippet that reproduces the behavior. Every accepted
report becomes a regression fixture under `tests/fixtures/`.
