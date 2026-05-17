/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CostEstimateResponse } from '../models/CostEstimateResponse';
import type { SettingsResponse } from '../models/SettingsResponse';
import type { SettingsUpdate } from '../models/SettingsUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SettingsService {
    /**
     * Get Settings Endpoint
     * Render the effective :class:`Settings` + detected providers as JSON.
     * @returns SettingsResponse Successful Response
     * @throws ApiError
     */
    public static getSettingsEndpointApiSettingsGet(): CancelablePromise<SettingsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/settings',
        });
    }
    /**
     * Put Settings
     * Apply a partial settings update.
     *
     * Only the fields explicitly set on ``payload`` overwrite state — None
     * values are treated as "leave unchanged" so the UI can PATCH a single
     * field without resending the rest. The merged state is reflected back
     * in the response so the frontend doesn't need a second GET.
     * @returns SettingsResponse Successful Response
     * @throws ApiError
     */
    public static putSettingsApiSettingsPut({
        requestBody,
    }: {
        requestBody: SettingsUpdate,
    }): CancelablePromise<SettingsResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/settings',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Cost Estimate
     * Estimate cost of running a prompt against *model* once.
     *
     * Token counts are rough — we use the classic "4 characters per token"
     * heuristic against the latest prompt-version template text. The
     * estimate is meant for "should I bother running this?" UX, not for
     * accounting.
     *
     * Raises 404 when the prompt has no stored template (e.g., scanner
     * saw the site but no version row was ever created) and 400 when the
     * model isn't in our pricebook.
     * @returns CostEstimateResponse Successful Response
     * @throws ApiError
     */
    public static getCostEstimateApiSettingsCostEstimateGet({
        promptId,
        model,
    }: {
        promptId: string,
        model: string,
    }): CancelablePromise<CostEstimateResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/settings/cost-estimate',
            query: {
                'prompt_id': promptId,
                'model': model,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
