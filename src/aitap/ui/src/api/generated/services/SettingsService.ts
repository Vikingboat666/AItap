/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CostEstimateResponse } from '../models/CostEstimateResponse';
import type { Defaults } from '../models/Defaults';
import type { SettingsResponse } from '../models/SettingsResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SettingsService {
    /**
     * Get Settings Endpoint
     * Render the effective :class:`Settings` + detected providers as JSON.
     *
     * Per-profile key status no longer rides on this response — clients
     * that need it call ``GET /api/profiles`` instead. The legacy
     * provider/model/judge_model fields stay so existing internal callers
     * that key off them keep working until they switch to the profiles
     * API.
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
     * Put Settings Defaults
     * Pick which configured profiles are the default model + judge.
     *
     * The route delegates validation + persistence to
     * :func:`aitap.server.routes.profiles.set_defaults` so the in-process
     * cache and the YAML mirror stay in lockstep. 422 + plain-language
     * detail when a referenced profile id doesn't exist; ``None`` on
     * either field clears the corresponding default.
     * @returns Defaults Successful Response
     * @throws ApiError
     */
    public static putSettingsDefaultsApiSettingsDefaultsPut({
        requestBody,
    }: {
        requestBody: Defaults,
    }): CancelablePromise<Defaults> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/settings/defaults',
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
