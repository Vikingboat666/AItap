/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunOutput } from './RunOutput';
export type RunDetailResponse = {
    cost_usd: number;
    finished_at: (string | null);
    outputs: Array<RunOutput>;
    run_id: string;
    started_at: string;
    status: 'running' | 'done' | 'failed';
    target_id: string;
    target_kind: 'prompt' | 'pipeline';
    target_version: number;
};

