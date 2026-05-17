/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HistoryResponse } from '../models/HistoryResponse';
import type { PromptVersionResponse } from '../models/PromptVersionResponse';
import type { RollbackRequest } from '../models/RollbackRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class HistoryService {
    /**
     * Get History
     * Return every recorded version of *prompt_id* plus average scores.
     *
     * We require the prompt to exist in the ``prompts`` table (404 if not)
     * so the frontend can surface "this prompt was deleted from the source
     * code" distinctly from "no versions yet" (which is an empty list).
     * @returns HistoryResponse Successful Response
     * @throws ApiError
     */
    public static getHistoryApiHistoryPromptIdGet({
        promptId,
    }: {
        promptId: string,
    }): CancelablePromise<HistoryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/history/{prompt_id}',
            path: {
                'prompt_id': promptId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rollback
     * Create a new head version whose content matches ``target_version``.
     *
     * Rollback is implemented as a forward step (no destructive delete) so
     * the audit trail stays intact — see :func:`aitap.store.history.perform_rollback`
     * for the lineage semantics.
     * @returns PromptVersionResponse Successful Response
     * @throws ApiError
     */
    public static rollbackApiHistoryPromptIdRollbackPost({
        promptId,
        requestBody,
    }: {
        promptId: string,
        requestBody: RollbackRequest,
    }): CancelablePromise<PromptVersionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/history/{prompt_id}/rollback',
            path: {
                'prompt_id': promptId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
