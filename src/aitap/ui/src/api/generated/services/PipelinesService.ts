/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PipelineDetailResponse } from '../models/PipelineDetailResponse';
import type { PipelineListResponse } from '../models/PipelineListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PipelinesService {
    /**
     * List Pipelines
     * Return one summary per detected pipeline.
     *
     * Summaries derive node/edge/entry/exit counts from the stored payload
     * rather than running a fresh DAG analysis — the scanner already
     * populated ``entry_points``/``exit_points`` and storing pre-computed
     * counts in the schema would be redundant denormalisation.
     * @returns PipelineListResponse Successful Response
     * @throws ApiError
     */
    public static listPipelinesApiPipelinesGet(): CancelablePromise<PipelineListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/pipelines',
        });
    }
    /**
     * Get Pipeline
     * Return a pipeline plus a prompt-id -> summary index for its nodes.
     *
     * The ``site_index`` lets the UI render every node label/file/line
     * without a follow-up ``GET /api/prompts/{id}`` per node. We tolerate
     * nodes that reference prompts no longer in the DB (e.g., a stale
     * pipeline payload after a re-scan removed a call site) by simply
     * omitting them from the index — the frontend renders an unknown node
     * placeholder.
     * @returns PipelineDetailResponse Successful Response
     * @throws ApiError
     */
    public static getPipelineApiPipelinesPipelineIdGet({
        pipelineId,
    }: {
        pipelineId: string,
    }): CancelablePromise<PipelineDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/pipelines/{pipeline_id}',
            path: {
                'pipeline_id': pipelineId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
