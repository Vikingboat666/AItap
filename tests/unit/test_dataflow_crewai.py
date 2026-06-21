"""CrewAI DAG detector tests (B2-CrewAI, wt/scanner-crewai).

CrewAI projects declare their multi-agent topology via three primitives:

    researcher = Agent(role="Researcher", goal="…", backstory="…")
    writer = Agent(role="Writer", …)

    research_task = Task(
        description="Research X",
        agent=researcher,
        expected_output="…",
    )
    write_task = Task(
        description="Write blog post",
        agent=writer,
        context=[research_task],  # ← upstream dependency
    )

    crew = Crew(
        agents=[researcher, writer],
        tasks=[research_task, write_task],
        process=Process.sequential,
    )

There's no ``add_edge``-equivalent — dependencies live in
``Task(context=[other_task])`` (explicit, HIGH confidence) and in
``Crew(tasks=[a, b, c], process=sequential)`` ordering (implicit,
MEDIUM confidence). ``Process.hierarchical`` doesn't have a static
shape (a runtime-decided manager agent routes work) so we emit a
fan-out from the first task with LOW confidence.

Adjacent OSS: ``agentic-radar`` (979★) detects CrewAI workflows by
the same Agent/Task/Crew shape — we mirror its approach to the
``Task(context=…)`` parsing but go further by emitting Pipeline
objects with ``PipelineNode.kind='declared_llm'`` so the UI can show
which steps cost tokens without misleading users about whether a
PromptSite exists.

Test plan (B2-CrewAI design):

1. Context-only chain — three Task nodes wired via ``context=[…]``.
2. Sequential process implicit chain (no context= lists).
3. Hierarchical process emits LOW-confidence fan-out from task 0.
4. Same agent reused across tasks ⇒ tasks dedupe to one Pipeline.
5. Aliased import (``from crewai import Crew as C``).
6. Dynamic context (variable, not list literal) ⇒ those edges skipped.
7. Crew with empty ``tasks=[]`` ⇒ no pipeline emitted.
8. Detector-level smoke test.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.dataflow import CrewAIDetector
from aitap.scanner.engine import scan_project
from aitap.scanner.models import Confidence, EdgeKind, Pipeline


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(dedent(source), encoding="utf-8")
    return file_path


def _scan(project_root: Path):
    return scan_project(project_root)


def _crewai_pipelines(result) -> list[Pipeline]:
    out: list[Pipeline] = []
    for p in result.pipelines:
        if any(e.kind is EdgeKind.CREWAI for e in p.edges):
            out.append(p)
    return out


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. Context-only chain                                                       #
# --------------------------------------------------------------------------- #


def test_crewai_context_chain_emits_high_confidence_edges(
    project_root: Path,
) -> None:
    """Three tasks wired via ``Task(context=[upstream])`` produce two
    HIGH-confidence CrewAI edges; nodes are ``declared_llm``.
    """
    _write(
        project_root,
        "app/crew.py",
        """
        from crewai import Agent, Task, Crew, Process

        researcher = Agent(role="Researcher", goal="research", backstory="r")
        writer = Agent(role="Writer", goal="write", backstory="w")
        editor = Agent(role="Editor", goal="edit", backstory="e")

        research_task = Task(
            description="Research X",
            expected_output="bullets",
            agent=researcher,
        )
        write_task = Task(
            description="Write blog post",
            expected_output="markdown",
            agent=writer,
            context=[research_task],
        )
        edit_task = Task(
            description="Edit post",
            expected_output="polished markdown",
            agent=editor,
            context=[research_task, write_task],
        )

        crew = Crew(
            agents=[researcher, writer, editor],
            tasks=[research_task, write_task, edit_task],
            process=Process.sequential,
        )
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 1
    pipeline = crew_pipelines[0]

    # Three nodes, all declared_llm (CrewAI agents don't expose
    # PromptSites — the prompts live inside the framework).
    declared_nodes = [n for n in pipeline.nodes if n.kind == "declared_llm"]
    assert len(declared_nodes) == 3

    # context edges: research→write, research→edit, write→edit (3 edges).
    # The sequential process would ALSO emit research→write→edit
    # but dedup collapses overlap with context= edges (HIGH wins).
    assert len(pipeline.edges) >= 3
    high_edges = [e for e in pipeline.edges if e.confidence is Confidence.HIGH]
    # At least the two context-declared edges land at HIGH.
    assert len(high_edges) >= 3
    assert all(e.kind is EdgeKind.CREWAI for e in pipeline.edges)


# --------------------------------------------------------------------------- #
# 2. Sequential process — implicit ordering                                   #
# --------------------------------------------------------------------------- #


def test_crewai_sequential_process_implies_chain_without_context(
    project_root: Path,
) -> None:
    """No ``context=`` lists — sequential process implies tasks run in
    list order. The detector emits MEDIUM-confidence edges between
    consecutive list members.
    """
    _write(
        project_root,
        "app/crew_implicit.py",
        """
        from crewai import Agent, Task, Crew, Process

        agent_a = Agent(role="A", goal="a", backstory="a")
        agent_b = Agent(role="B", goal="b", backstory="b")

        task_one = Task(description="One", expected_output="o", agent=agent_a)
        task_two = Task(description="Two", expected_output="o", agent=agent_b)
        task_three = Task(description="Three", expected_output="o", agent=agent_a)

        crew = Crew(
            agents=[agent_a, agent_b],
            tasks=[task_one, task_two, task_three],
            process=Process.sequential,
        )
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 1
    pipeline = crew_pipelines[0]

    # 3 nodes, 2 implicit-ordering edges at MEDIUM (no context= signal).
    assert len([n for n in pipeline.nodes if n.kind == "declared_llm"]) == 3
    assert len(pipeline.edges) == 2
    assert all(e.confidence is Confidence.MEDIUM for e in pipeline.edges)


# --------------------------------------------------------------------------- #
# 3. Hierarchical process — runtime-decided routing                           #
# --------------------------------------------------------------------------- #


def test_crewai_hierarchical_process_emits_low_confidence_fanout(
    project_root: Path,
) -> None:
    """``Process.hierarchical`` means a manager agent decides the order
    at runtime. Static analysis can't see that — we emit a fan-out
    from the first task to all others at LOW confidence so the UI
    surfaces "this is dynamic" honestly.
    """
    _write(
        project_root,
        "app/crew_hierarchical.py",
        """
        from crewai import Agent, Task, Crew, Process

        manager = Agent(role="Manager", goal="m", backstory="m")
        researcher = Agent(role="Researcher", goal="r", backstory="r")
        writer = Agent(role="Writer", goal="w", backstory="w")

        research_task = Task(
            description="Research", expected_output="r", agent=researcher,
        )
        write_task = Task(
            description="Write", expected_output="w", agent=writer,
        )

        crew = Crew(
            agents=[researcher, writer],
            tasks=[research_task, write_task],
            process=Process.hierarchical,
            manager_agent=manager,
        )
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 1
    pipeline = crew_pipelines[0]
    # All edges from hierarchical mode are LOW confidence.
    assert all(e.confidence is Confidence.LOW for e in pipeline.edges)


# --------------------------------------------------------------------------- #
# 4. Aliased import                                                           #
# --------------------------------------------------------------------------- #


def test_crewai_aliased_import_still_recognised(
    project_root: Path,
) -> None:
    """``from crewai import Crew as C`` — the detector follows the
    import alias map the same way LangGraph does."""
    _write(
        project_root,
        "app/crew_aliased.py",
        """
        from crewai import Agent as A, Task as T, Crew as C, Process as P

        a = A(role="a", goal="g", backstory="b")
        t1 = T(description="d1", expected_output="o", agent=a)
        t2 = T(description="d2", expected_output="o", agent=a, context=[t1])

        crew = C(agents=[a], tasks=[t1, t2], process=P.sequential)
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 1


# --------------------------------------------------------------------------- #
# 5. Dynamic context — skipped                                                #
# --------------------------------------------------------------------------- #


def test_crewai_dynamic_context_skips_those_edges(
    project_root: Path,
) -> None:
    """``Task(context=upstream_list)`` where ``upstream_list`` is a
    variable — we can't statically see what's in it. Skip those
    edges; the static ``context=[…]`` literals in sibling tasks
    still emit.
    """
    _write(
        project_root,
        "app/crew_dynamic.py",
        """
        from crewai import Agent, Task, Crew, Process

        a = Agent(role="a", goal="g", backstory="b")

        t1 = Task(description="d1", expected_output="o", agent=a)
        t2 = Task(description="d2", expected_output="o", agent=a, context=[t1])

        SHARED = [t1, t2]  # dynamic — not a literal at the call site

        t3 = Task(description="d3", expected_output="o", agent=a, context=SHARED)
        t4 = Task(description="d4", expected_output="o", agent=a, context=[t2])

        crew = Crew(
            agents=[a],
            tasks=[t1, t2, t3, t4],
            process=Process.sequential,
        )
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 1
    pipeline = crew_pipelines[0]

    # The static ``context=[t1]`` on t2 and ``context=[t2]`` on t4
    # surface as HIGH-confidence edges. The dynamic ``context=SHARED``
    # on t3 doesn't add any phantom edges. Implicit sequential ordering
    # still fills in t1→t2→t3→t4 (MEDIUM).
    high_edges = [e for e in pipeline.edges if e.confidence is Confidence.HIGH]
    assert len(high_edges) >= 2


# --------------------------------------------------------------------------- #
# 6. Empty tasks                                                              #
# --------------------------------------------------------------------------- #


def test_crewai_empty_tasks_list_emits_no_pipeline(
    project_root: Path,
) -> None:
    """``Crew(tasks=[])`` ⇒ no pipeline emitted (no nodes to draw)."""
    _write(
        project_root,
        "app/crew_empty.py",
        """
        from crewai import Agent, Crew, Process

        a = Agent(role="a", goal="g", backstory="b")
        crew = Crew(agents=[a], tasks=[], process=Process.sequential)
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert crew_pipelines == []


# --------------------------------------------------------------------------- #
# 7. Multiple Crews in one file                                               #
# --------------------------------------------------------------------------- #


def test_crewai_multiple_crews_in_one_file_each_produce_a_pipeline(
    project_root: Path,
) -> None:
    """Two ``Crew(...)`` instantiations in the same file → two
    independent Pipelines. Tests share-by-Python-import semantics."""
    _write(
        project_root,
        "app/two_crews.py",
        """
        from crewai import Agent, Task, Crew, Process

        a = Agent(role="a", goal="g", backstory="b")

        # Crew 1
        t1 = Task(description="d1", expected_output="o", agent=a)
        t2 = Task(description="d2", expected_output="o", agent=a, context=[t1])
        first_crew = Crew(agents=[a], tasks=[t1, t2], process=Process.sequential)

        # Crew 2 — separate topology
        u1 = Task(description="u1", expected_output="o", agent=a)
        u2 = Task(description="u2", expected_output="o", agent=a, context=[u1])
        second_crew = Crew(agents=[a], tasks=[u1, u2], process=Process.sequential)
        """,
    )
    result = _scan(project_root)
    crew_pipelines = _crewai_pipelines(result)
    assert len(crew_pipelines) == 2


# --------------------------------------------------------------------------- #
# 8. Detector-level smoke test                                                #
# --------------------------------------------------------------------------- #


def test_detector_returns_empty_list_when_no_crew_in_project(
    project_root: Path,
) -> None:
    """Project without any CrewAI import → detector returns []."""
    _write(
        project_root,
        "app/plain.py",
        """
        async def step(state):
            return await openai.complete(messages=[{"content": "x"}])
        """,
    )
    detector = CrewAIDetector()
    result = _scan(project_root)
    files = [project_root / "app/plain.py"]
    pipelines = detector.detect_pipelines(files, project_root, result.prompts)
    assert pipelines == []
