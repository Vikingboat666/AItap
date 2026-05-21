/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IterateSessionRequest } from '../models/IterateSessionRequest';
import type { IterateSessionResponse } from '../models/IterateSessionResponse';
import type { IterationView } from '../models/IterationView';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class IterateService {
    /**
     * Start Iterate Session
     * Kick off an iterate session.
     *
     * Returns 202 + a fresh ``session_id`` *before* the background task
     * runs the full critique-and-revise loop. The route writes a
     * placeholder row so an immediate ``GET /api/iterations/{session_id}``
     * succeeds; the placeholder is deleted (or replaced with a sentinel)
     * when the background task finishes.
     *
     * Mode validation happens here (not in the loop) so a malformed
     * request never spins up a task that has to write a sentinel row to
     * surface the failure — a 400 is friendlier and matches the
     * request-validation conventions of the rest of the API surface.
     * @returns IterateSessionResponse Successful Response
     * @throws ApiError
     */
    public static startIterateSessionApiIteratePost({
        requestBody,
    }: {
        requestBody: IterateSessionRequest,
    }): CancelablePromise<IterateSessionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/iterate',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Iterations For Prompt
     * Return iterations for *prompt_id*, newest first, capped at *limit*.
     *
     * Sorted by ``started_at DESC`` (then id DESC for ties) by the DAO.
     * Placeholders are filtered so the History UI never shows the
     * transient round=0 marker.
     * @returns IterationView Successful Response
     * @throws ApiError
     */
    public static listIterationsForPromptApiIterationsByPromptPromptIdGet({
        promptId,
        limit = 50,
    }: {
        promptId: string,
        limit?: number,
    }): CancelablePromise<Array<IterationView>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/iterations/by-prompt/{prompt_id}',
            path: {
                'prompt_id': promptId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Iterate Session
     * Return the full session state — every iteration row + derived status.
     * @returns IterateSessionResponse Successful Response
     * @throws ApiError
     */
    public static getIterateSessionApiIterationsSessionIdGet({
        sessionId,
    }: {
        sessionId: string,
    }): CancelablePromise<IterateSessionResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/iterations/{session_id}',
            path: {
                'session_id': sessionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Iterate Session Latest
     * Return the highest-round iteration row in the session.
     *
     * This is the polling shortcut for UIs that don't want to fetch the
     * whole list every second. Placeholder rows (round=0) are excluded so
     * the result tracks the latest *real* loop progress.
     * @returns IterationView Successful Response
     * @throws ApiError
     */
    public static getIterateSessionLatestApiIterationsSessionIdLatestGet({
        sessionId,
    }: {
        sessionId: string,
    }): CancelablePromise<IterationView> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/iterations/{session_id}/latest',
            path: {
                'session_id': sessionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
