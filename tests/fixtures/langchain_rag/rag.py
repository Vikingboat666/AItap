"""Sample LangChain RAG chain — three LLM hops connected via the | operator.

Used as a fixture for the dataflow detector: the test asserts the scanner
identifies three PromptSites and one Pipeline with two edges connecting them.

The chain composition is intentionally idiomatic LCEL so we exercise the
``BinOp(BitOr)`` recognition path, not just contrived AST.
"""

from __future__ import annotations

# These imports are intentionally for show — the file is only ever scanned,
# never executed. langchain_core / langchain_openai do not need to be
# installed for the fixture to exist.
try:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - fixture is scan-only
    pass

from openai import OpenAI

client = OpenAI()


def retrieval_prompt(query: str) -> str:
    return (
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Rewrite the user's question for better retrieval."},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
        )
        .choices[0]
        .message.content
        or ""
    )


def synthesise(rewritten: str, context: str) -> str:
    return (
        client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Answer using only the provided context."},
                {"role": "user", "content": f"Question: {rewritten}\n\nContext:\n{context}"},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        .choices[0]
        .message.content
        or ""
    )


def critique(answer: str) -> str:
    return (
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Critique the answer for hallucinations."},
                {"role": "user", "content": answer},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        .choices[0]
        .message.content
        or ""
    )


def run(query: str, context: str) -> str:
    rewritten = retrieval_prompt(query)
    answer = synthesise(rewritten, context)
    return critique(answer)
