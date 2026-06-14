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
    id: string;
    prompt_id: string;
    round: number;
    session_id: string;
    is_baseline: boolean;
    parent_version?: (number | null);
    new_version?: (number | null);
    revise_mode?: ('auto' | 'guided' | 'manual' | 'failed' | null);
    revise_instruction?: (string | null);
    critique_text?: (string | null);
    weighted_score: number;
    per_dim_scores?: Record<string, number>;
    downstream_status?: (Record<string, string> | null);
    converged_reason?: ('max_rounds' | 'delta' | 'stagnation' | 'absolute' | 'critic_failed' | null);
    started_at: string;
    finished_at?: (string | null);
};

