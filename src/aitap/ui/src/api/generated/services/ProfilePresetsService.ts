/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProfilePreset } from '../models/ProfilePreset';
import type { ProfilePresetsUpdate } from '../models/ProfilePresetsUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProfilePresetsService {
    /**
     * Reset Profile Presets
     * Restore the seeded starter set, overwriting any user edits.
     *
     * Returns the freshly-seeded list so the editor can re-render
     * without a second round-trip. Idempotent.
     * @returns ProfilePreset Successful Response
     * @throws ApiError
     */
    public static resetProfilePresetsApiProfilePresetsDelete(): CancelablePromise<Array<ProfilePreset>> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/profile-presets',
        });
    }
    /**
     * List Profile Presets
     * Return the current preset list (seeding on first launch).
     * @returns ProfilePreset Successful Response
     * @throws ApiError
     */
    public static listProfilePresetsApiProfilePresetsGet(): CancelablePromise<Array<ProfilePreset>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/profile-presets',
        });
    }
    /**
     * Replace Profile Presets
     * Persist *payload.presets* as the whole new list.
     *
     * Replace-in-full semantics mirror the editor's "Save" UX: the user
     * sees the list, edits rows in place, hits Save once. The empty list
     * is a legitimate persisted state — the user explicitly cleared
     * every preset and the chip row will render empty. To get the
     * seeded set back the user hits Reset (the DELETE endpoint below).
     * @returns ProfilePreset Successful Response
     * @throws ApiError
     */
    public static replaceProfilePresetsApiProfilePresetsPut({
        requestBody,
    }: {
        requestBody: ProfilePresetsUpdate,
    }): CancelablePromise<Array<ProfilePreset>> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/profile-presets',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
