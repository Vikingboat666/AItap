/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CostEstimateResponse } from '../models/CostEstimateResponse';
import type { ProviderKeyStatus } from '../models/ProviderKeyStatus';
import type { SetKeyRequest } from '../models/SetKeyRequest';
import type { SettingsResponse } from '../models/SettingsResponse';
import type { SettingsUpdate } from '../models/SettingsUpdate';
import type { TestKeyResponse } from '../models/TestKeyResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SettingsService {
    /**
     * Get Settings Endpoint
     * Render the effective :class:`Settings` + detected providers as JSON.
     *
     * The ``keys`` field is additive (CONTRACTS.md): each entry reports
     * the per-provider ``{configured, source, masked}`` triple from
     * :mod:`aitap.secrets`. The raw key value is never exposed.
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
    /**
     * Set Provider Key
     * Persist *payload.key* for *payload.provider*.
     *
     * The response body is intentionally a :class:`ProviderKeyStatus` —
     * metadata only. We never echo the submitted key (not in the response,
     * not in the log filter, not in the SQLite store). The client should
     * immediately drop the typed-key React state on success and rely on
     * the returned masked preview.
     * @returns ProviderKeyStatus Successful Response
     * @throws ApiError
     */
    public static setProviderKeyApiSettingsKeyPost({
        requestBody,
    }: {
        requestBody: SetKeyRequest,
    }): CancelablePromise<ProviderKeyStatus> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/settings/key',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Provider Key
     * Delete *provider*'s key from every store aitap manages.
     *
     * Real delete (``keyring.delete_password`` / fallback-file entry
     * removal), not an overwrite. The response reflects whatever the
     * resolver sees afterwards — which may be ``source='env'`` if the
     * user also has the env var set; the UI uses that signal to remind
     * them to clear their shell config.
     * @returns ProviderKeyStatus Successful Response
     * @throws ApiError
     */
    public static deleteProviderKeyApiSettingsKeyProviderDelete({
        provider,
    }: {
        provider: string,
    }): CancelablePromise<ProviderKeyStatus> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/settings/key/{provider}',
            path: {
                'provider': provider,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Test Provider Key
     * Probe *provider* with one minimal LLM call to confirm the key works.
     *
     * Anthropic: ``/v1/messages`` with ``[{"role":"user","content":"ping"}]``
     * and ``max_tokens=4``. OpenAI: the equivalent ``chat.completions`` call.
     * The response is a :class:`TestKeyResponse` — never the raw key,
     * never a stack trace, never a status code in the message. The
     * ``detail`` field is the plain-language sentence the UI surfaces in
     * the test card.
     * @returns TestKeyResponse Successful Response
     * @throws ApiError
     */
    public static testProviderKeyApiSettingsTestProviderPost({
        provider,
    }: {
        provider: string,
    }): CancelablePromise<TestKeyResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/settings/test/{provider}',
            path: {
                'provider': provider,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
