"""Per-detector unit tests using inline source snippets.

We synthesise PromptSites that match the line numbers of the calls in the
snippet, then drive each detector directly. This isolates the dataflow
logic from the broader scanner — a regression in the prompt extractor
won't make these tests false-positive.
"""

from __future__ import annotations

import ast
from pathlib import Path

from aitap.scanner.dataflow.intra_file_chain import IntraFileChain
from aitap.scanner.dataflow.langchain_pipe import LangChainPipe
from aitap.scanner.dataflow.llamaindex_engine import LlamaIndexEngine
from aitap.scanner.dataflow.variable_tracker import VariableTracker
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    PromptSite,
    Provider,
    Role,
)


def _make_site(name: str, line: int) -> PromptSite:
    return PromptSite(
        id=f"site-{name}",
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=line, line_end=line),
        messages=[Message(role=Role.USER, template_text="x")],
        parameters=CallParameters(),
        confidence=Confidence.HIGH,
    )


def _parse(src: str) -> ast.Module:
    return ast.parse(src)


# --------------------------------------------------------------------------- #
# VariableTracker                                                             #
# --------------------------------------------------------------------------- #


def test_variable_tracker_finds_simple_chain() -> None:
    # Line 1: def f():
    # Line 2:     a = call_a()
    # Line 3:     return call_b(a)
    src = "def f():\n    a = call_a()\n    return call_b(a)\n"
    sites = [_make_site("a", 2), _make_site("b", 3)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    e = edges[0]
    assert e.source == "site-a"
    assert e.target == "site-b"
    assert e.kind == EdgeKind.VARIABLE
    assert e.via == "a"
    assert e.confidence == Confidence.HIGH


def test_variable_tracker_handles_kwarg_use() -> None:
    src = "def f():\n    a = call_a()\n    return call_b(text=a)\n"
    sites = [_make_site("a", 2), _make_site("b", 3)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    assert edges[0].via == "a"


def test_variable_tracker_handles_deeply_nested_use() -> None:
    """Real OpenAI-style call: variable used inside messages=[{"content": var}]."""
    src = (
        "def f():\n    a = call_a()\n    return call_b(messages=[{'role': 'user', 'content': a}])\n"
    )
    sites = [_make_site("a", 2), _make_site("b", 3)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    assert edges[0].via == "a"


def test_variable_tracker_downgrades_confidence_in_nested_block() -> None:
    src = "def f():\n    a = call_a()\n    if cond:\n        return call_b(a)\n"
    sites = [_make_site("a", 2), _make_site("b", 4)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    assert edges[0].confidence == Confidence.MEDIUM


def test_variable_tracker_skips_when_no_chain() -> None:
    src = "def f():\n    a = call_a()\n    return a\n"
    sites = [_make_site("a", 2)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert edges == []


def test_variable_tracker_does_not_cross_function_scope() -> None:
    src = "def f():\n    a = call_a()\n\ndef g():\n    return call_b(a)\n"
    sites = [_make_site("a", 2), _make_site("b", 5)]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert edges == []


def test_variable_tracker_self_edge_suppressed() -> None:
    src = "def f():\n    a = call_a()\n    return call_a(a)\n"
    site = _make_site("a", 2)
    sites = [site]
    edges = VariableTracker().detect(_parse(src), sites, Path("x.py"))
    assert edges == []


# --------------------------------------------------------------------------- #
# LangChainPipe                                                               #
# --------------------------------------------------------------------------- #


def test_langchain_pipe_finds_chain() -> None:
    # Line 1: chain = (
    # Line 2:     call_a()
    # Line 3:     | call_b()
    # Line 4:     | call_c()
    # Line 5: )
    src = "chain = (\n    call_a()\n    | call_b()\n    | call_c()\n)\n"
    sites = [_make_site("a", 2), _make_site("b", 3), _make_site("c", 4)]
    edges = LangChainPipe().detect(_parse(src), sites, Path("x.py"))
    # ast.walk visits the outer + nested BinOp; the (a, b) edge is emitted
    # twice (once per BinOp). The orchestrator dedupes; the detector test
    # asserts on unique pairs to stay implementation-agnostic.
    pairs = {(e.source, e.target) for e in edges}
    assert pairs == {("site-a", "site-b"), ("site-b", "site-c")}
    assert all(e.kind == EdgeKind.LANGCHAIN_PIPE for e in edges)


def test_langchain_pipe_ignores_unrelated_binops() -> None:
    src = "x = 2 | 3\n"
    edges = LangChainPipe().detect(
        _parse(src), [_make_site("a", 1), _make_site("b", 1)], Path("x.py")
    )
    assert edges == []


def test_langchain_pipe_skips_chain_with_unknown_operand() -> None:
    src = "chain = (\n    call_a()\n    | unknown_var\n    | call_c()\n)\n"
    sites = [_make_site("a", 2), _make_site("c", 4)]
    edges = LangChainPipe().detect(_parse(src), sites, Path("x.py"))
    # No adjacent pair of known sites, so no edges.
    assert edges == []


# --------------------------------------------------------------------------- #
# IntraFileChain                                                              #
# --------------------------------------------------------------------------- #


def test_intra_file_chain_links_function_wrappers() -> None:
    # Line 1: def summarise(text):
    # Line 2:     return call_a()
    # Line 3:
    # Line 4: def critique(text):
    # Line 5:     return call_b()
    # Line 6:
    # Line 7: result = critique(summarise("hi"))
    src = (
        "def summarise(text):\n"
        "    return call_a()\n"
        "\n"
        "def critique(text):\n"
        "    return call_b()\n"
        "\n"
        'result = critique(summarise("hi"))\n'
    )
    sites = [_make_site("a", 2), _make_site("b", 5)]
    edges = IntraFileChain().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    e = edges[0]
    assert e.source == "site-a"
    assert e.target == "site-b"
    assert e.kind == EdgeKind.FUNCTION


def test_intra_file_chain_ignores_method_calls() -> None:
    """self.x() is cross-class state which is v0.2 scope."""
    src = (
        "class C:\n"
        "    def summarise(self, text):\n"
        "        return call_a()\n"
        "\n"
        "    def critique(self, text):\n"
        "        return call_b()\n"
        "\n"
        "    def run(self, text):\n"
        "        return self.critique(self.summarise(text))\n"
    )
    sites = [_make_site("a", 3), _make_site("b", 6)]
    edges = IntraFileChain().detect(_parse(src), sites, Path("x.py"))
    assert edges == []


def test_intra_file_chain_handles_attribute_returns() -> None:
    src = (
        "def summarise(text):\n"
        "    return call_a().choices[0].message.content\n"
        "\n"
        "def critique(text):\n"
        "    return call_b().content[0].text\n"
        "\n"
        'result = critique(summarise("hi"))\n'
    )
    sites = [_make_site("a", 2), _make_site("b", 5)]
    edges = IntraFileChain().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1


# --------------------------------------------------------------------------- #
# LlamaIndexEngine                                                            #
# --------------------------------------------------------------------------- #


def test_llamaindex_engine_emits_unresolved_low_confidence_edges() -> None:
    # Line 1: from llama_index.core import VectorStoreIndex
    # Line 2:
    # Line 3: def build():
    # Line 4:     qe = index.as_query_engine(llm=call_a())
    # Line 5:     return qe
    # Line 6:
    # Line 7: resp = call_b()
    src = (
        "from llama_index.core import VectorStoreIndex\n"
        "\n"
        "def build():\n"
        "    qe = index.as_query_engine(llm=call_a())\n"
        "    return qe\n"
        "\n"
        "resp = call_b()\n"
    )
    sites = [_make_site("a", 4), _make_site("b", 7)]
    edges = LlamaIndexEngine().detect(_parse(src), sites, Path("x.py"))
    assert len(edges) == 1
    e = edges[0]
    assert e.kind == EdgeKind.LLAMAINDEX
    assert e.confidence == Confidence.LOW


def test_llamaindex_engine_skips_files_without_llamaindex_import() -> None:
    src = "qe = index.as_query_engine(llm=call_a())\nresp = call_b()\n"
    sites = [_make_site("a", 1), _make_site("b", 2)]
    edges = LlamaIndexEngine().detect(_parse(src), sites, Path("x.py"))
    assert edges == []
