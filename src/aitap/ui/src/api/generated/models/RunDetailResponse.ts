/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunOutput } from './RunOutput';
export type RunDetailResponse = {
    run_id: string;
    target_kind: 'prompt' | 'pipeline';
    target_id: string;
    target_version: number;
    status: 'running' | 'done' | 'failed';
    outputs: Array<RunOutput>;
    cost_usd: number;
    started_at: string;
    finished_at: (string | null);
};

