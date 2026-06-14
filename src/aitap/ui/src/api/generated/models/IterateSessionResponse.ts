/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IterationView } from './IterationView';
/**
 * Aggregated session view: status + all iteration rows.
 */
export type IterateSessionResponse = {
    session_id: string;
    status: 'running' | 'converged' | 'failed';
    converged_reason?: (string | null);
    iterations?: Array<IterationView>;
    final_version?: (number | null);
};

