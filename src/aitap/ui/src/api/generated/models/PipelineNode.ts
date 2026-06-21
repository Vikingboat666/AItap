/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A node in the pipeline DAG, referencing a PromptSite by id.
 *
 * ``kind`` distinguishes LLM call sites from helper steps the DAG
 * declares but that don't themselves invoke an LLM (LangGraph's
 * ``add_node("parse", parse_json)`` shape). Default ``"llm"`` keeps
 * every pre-LangGraph fixture unchanged; ``"non_llm"`` lets the UI
 * render the node distinctly (dashed border / dimmer color) so a
 * user reading the DAG can tell which steps actually cost tokens.
 */
export type PipelineNode = {
    prompt_id: string;
    label?: (string | null);
    kind?: 'llm' | 'non_llm';
};

