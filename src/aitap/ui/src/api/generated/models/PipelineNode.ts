/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A node in the pipeline DAG, referencing a PromptSite by id.
 *
 * ``kind`` distinguishes three flavours of DAG step the UI renders
 * distinctly:
 *
 * - ``"llm"`` (default) — a real LLM call site backed by a concrete
 * :class:`PromptSite` in the user's code. Solid blue border.
 * - ``"non_llm"`` — a step the framework declared but that doesn't
 * itself invoke an LLM (LangGraph's ``add_node("parse",
 * parse_json)`` shape). Dashed grey border + dim opacity so users
 * can see at a glance that it doesn't cost tokens.
 * - ``"declared_llm"`` — a real LLM call whose prompt lives **inside
 * the framework**, not in code we can scan (CrewAI's ``Task(
     * description="…", agent=researcher)`` — runtime composes the
     * prompt from the agent's role/goal/backstory + the task
     * description). Solid green border. Distinct from ``"llm"``
     * because we can't link it to a PromptSite, and distinct from
     * ``"non_llm"`` because it *does* cost tokens. The playground
     * runner rejects these the same way it rejects ``non_llm``
     * (CrewAI runtime owns execution, aitap can't drive it).
     */
    export type PipelineNode = {
        prompt_id: string;
        label?: (string | null);
        kind?: 'llm' | 'non_llm' | 'declared_llm';
    };

