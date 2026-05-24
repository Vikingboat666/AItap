/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FeedbackCreate } from '../models/FeedbackCreate';
import type { FeedbackResponse } from '../models/FeedbackResponse';
import type { IterateRequest } from '../models/IterateRequest';
import type { IterateResponse } from '../models/IterateResponse';
import type { RunCreate } from '../models/RunCreate';
import type { RunDetailResponse } from '../models/RunDetailResponse';
import type { RunListResponse } from '../models/RunListResponse';
import type { RunResponse } from '../models/RunResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class RunsService {
    /**
     * List Runs Endpoint
     * List runs, optionally filtered by ``target_id``.
     *
     * ``limit`` is clamped to [1, 200] so a malicious query can't drain the
     * table. The frontend's default page size is 50.
     * @returns RunListResponse Successful Response
     * @throws ApiError
     */
    public static listRunsEndpointApiRunsGet({
        targetId,
        limit = 50,
    }: {
        targetId?: (string | null),
        limit?: number,
    }): CancelablePromise<RunListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/runs',
            query: {
                'target_id': targetId,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Run
     * Queue a new run.
     *
     * Wave 3 contract:
     * - Validate the payload (pydantic handles shape).
     * - Insert a ``runs`` row in the *running* state.
     * - Attempt to hand off to :mod:`aitap.playground.dispatch` via a lazy
     * import. The adapter is responsible for marking the run *done* /
     * *failed* and stamping the final cost. If the module is unavailable
     * we leave the row in *running* so a later worktree merge can attach.
     *
     * Wave 5 addition (A·D1/A·D3): pipeline runs carry an explicit
     * ``pipeline_mode`` plus mode-specific selectors. We validate their
     * consistency here — *before* writing the runs row — so a malformed
     * request 422s cleanly without leaving an orphan ``running`` row behind.
     * @returns RunResponse Successful Response
     * @throws ApiError
     */
    public static createRunApiRunsPost({
        requestBody,
    }: {
        requestBody: RunCreate,
    }): CancelablePromise<RunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/runs',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Run
     * Fetch one run + per-case outputs from the JSONL sidecar.
     *
     * Per-case outputs live in
     * ``<runs_dir>/<run_id>/outputs.jsonl`` (written by
     * :func:`aitap.playground.dispatch._write_outputs_sidecar`). Runs still
     * in the ``running`` status — or runs that failed at the run level
     * before any case completed — have no sidecar file; :func:`_load_outputs`
     * returns an empty list in that case so the contract shape is preserved.
     * @returns RunDetailResponse Successful Response
     * @throws ApiError
     */
    public static getRunApiRunsRunIdGet({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<RunDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/runs/{run_id}',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Feedback
     * Attach a feedback record to a run case.
     * @returns FeedbackResponse Successful Response
     * @throws ApiError
     */
    public static postFeedbackApiRunsRunIdFeedbackPost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: FeedbackCreate,
    }): CancelablePromise<FeedbackResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/runs/{run_id}/feedback',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Post Iterate
     * Fire one round of self-iteration based on collected feedback.
     *
     * Wave 3 implementation is a stub: it writes a new ``prompt_versions``
     * row attributed to ``created_by='iteration'`` so the rest of the
     * pipeline (history, diff, rollback) sees a real, queryable record.
     * The LLM-driven rewrite lands in M4 and will honour the
     * ``judge_model``/``convergence_threshold``/``include_downstream`` knobs
     * on ``payload`` — captured here so the API surface stays stable.
     * @returns IterateResponse Successful Response
     * @throws ApiError
     */
    public static postIterateApiRunsRunIdIteratePost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: IterateRequest,
    }): CancelablePromise<IterateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/runs/{run_id}/iterate',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
