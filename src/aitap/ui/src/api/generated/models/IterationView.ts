/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * API projection of one ``iterations`` row.
 *
 * Datetimes serialise as ISO-8601 strings. The shape is deliberately
 * flat — no nested objects — so the React UI's table rendering
 * consumes it without further normalisation.
 */
export type IterationView = {
    converged_reason?: ('max_rounds' | 'delta' | 'stagnation' | 'absolute' | 'critic_failed' | null);
    critique_text?: (string | null);
    downstream_status?: (Record<string, string> | null);
    finished_at?: (string | null);
    id: string;
    is_baseline: boolean;
    new_version?: (number | null);
    parent_version?: (number | null);
    per_dim_scores?: Record<string, number>;
    prompt_id: string;
    revise_instruction?: (string | null);
    revise_mode?: ('auto' | 'guided' | 'manual' | 'failed' | null);
    round: number;
    session_id: string;
    started_at: string;
    weighted_score: number;
};

