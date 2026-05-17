/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PromptDetailResponse } from '../models/PromptDetailResponse';
import type { PromptListResponse } from '../models/PromptListResponse';
import type { PromptVersionCreate } from '../models/PromptVersionCreate';
import type { PromptVersionResponse } from '../models/PromptVersionResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PromptsService {
    /**
     * List Prompts
     * Return every prompt detected by the most recent scan.
     *
     * ``latest_version`` is the highest ``prompt_versions.version`` for
     * the prompt, or 0 when no version has been recorded yet — the UI
     * treats 0 as "discovered but never edited" and offers a "record v1"
     * affordance.
     * @returns PromptListResponse Successful Response
     * @throws ApiError
     */
    public static listPromptsApiPromptsGet(): CancelablePromise<PromptListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prompts',
        });
    }
    /**
     * Get Prompt
     * Return one prompt's payload plus its complete version history.
     *
     * Returns 404 when the id is unknown — the frontend uses that to
     * redirect away from stale bookmarks rather than rendering a blank
     * detail page.
     * @returns PromptDetailResponse Successful Response
     * @throws ApiError
     */
    public static getPromptApiPromptsPromptIdGet({
        promptId,
    }: {
        promptId: string,
    }): CancelablePromise<PromptDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/prompts/{prompt_id}',
            path: {
                'prompt_id': promptId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Prompt Version
     * Record a new version of *prompt_id*.
     *
     * The body carries the edited messages + parameters; we don't try to
     * diff against the previous head here (that's a UI concern). We do
     * validate that the prompt exists so callers can't seed orphan version
     * rows by typo'ing an id.
     * @returns PromptVersionResponse Successful Response
     * @throws ApiError
     */
    public static createPromptVersionApiPromptsPromptIdVersionsPost({
        promptId,
        requestBody,
    }: {
        promptId: string,
        requestBody: PromptVersionCreate,
    }): CancelablePromise<PromptVersionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/prompts/{prompt_id}/versions',
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
