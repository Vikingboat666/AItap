/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Trigger one round of self-iteration based on collected feedback for the run.
 */
export type IterateRequest = {
    judge_model?: (string | null);
    max_iterations?: number;
    convergence_threshold?: number;
    include_downstream?: boolean;
};

