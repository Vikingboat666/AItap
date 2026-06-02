/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Profile } from '../models/Profile';
import type { ProfileTestResponse } from '../models/ProfileTestResponse';
import type { ProfileUpsertRequest } from '../models/ProfileUpsertRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProfilesService {
    /**
     * List Profiles
     * List every configured profile with its current key status.
     * @returns Profile Successful Response
     * @throws ApiError
     */
    public static listProfilesApiProfilesGet(): CancelablePromise<Array<Profile>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/profiles',
        });
    }
    /**
     * Create Profile
     * Create a new profile, optionally storing its key in one shot.
     *
     * The slug is derived from the label per :func:`slugify_label`;
     * collisions append ``-2``/``-3``/etc. If ``payload.api_key`` is
     * present we set the key first, then persist the metadata — this
     * ordering means a 409 from the keyring path doesn't leave a
     * keyless-but-persisted profile lying around.
     * @returns Profile Successful Response
     * @throws ApiError
     */
    public static createProfileApiProfilesPost({
        requestBody,
    }: {
        requestBody: ProfileUpsertRequest,
    }): CancelablePromise<Profile> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/profiles',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Profile
     * Remove a profile and (if needed) clear it from the defaults.
     *
     * Real delete: the keyring entry is removed (``delete_password``),
     * the YAML row is dropped, and any :class:`DefaultsConfig` reference
     * is auto-nulled per Decision 1. The response is the *final* shape
     * of the profile (configured=False, source="none") so the UI can
     * flip the row out of the list and update the defaults card in one
     * state update.
     * @returns Profile Successful Response
     * @throws ApiError
     */
    public static deleteProfileApiProfilesProfileIdDelete({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<Profile> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/profiles/{profile_id}',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Profile
     * Mutate everything but the id on an existing profile.
     *
     * The id is immutable by contract — a relabel doesn't drift the
     * keyring entry (which would orphan the user's key) and downstream
     * references (``defaults.model_profile_id``, run history rows) stay
     * valid. If the user wants a clean break, they delete + re-add.
     * @returns Profile Successful Response
     * @throws ApiError
     */
    public static updateProfileApiProfilesProfileIdPut({
        profileId,
        requestBody,
    }: {
        profileId: string,
        requestBody: ProfileUpsertRequest,
    }): CancelablePromise<Profile> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/profiles/{profile_id}',
            path: {
                'profile_id': profileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Test Profile
     * Connectivity probe for a profile's key.
     *
     * Resolves the key from the vault, builds a per-profile
     * :class:`~aitap.deep.client.LLMClient` via
     * :func:`~aitap.deep.factory.get_client_for_profile`, and issues a
     * single minimal chat call: ``messages=[{role:"user", content:"ping"}]``
     * with ``max_tokens=4`` (Decision 3 in ``docs/profiles-design.md`` —
     * same shape for both protocols).
     *
     * Exception → reason mapping:
     *
     * - :class:`ProviderAuthError`     → ``"auth"`` — the key is wrong / revoked.
     * - :class:`ProviderRateLimitError` → ``"rate_limit"`` — key works, just busy.
     * - :class:`ProviderError`         → ``"network"`` — couldn't reach the host.
     * - Anything else                  → ``"other"`` — log + opaque detail.
     *
     * None of the response detail strings ever include the raw exception
     * message: SDK exceptions have historically embedded request payloads
     * (Authorization header, body) in their ``str()`` (B2 regression from
     * PR #35). The detail copy is static + plain-language; the maintainer
     * sees the real exception in the log with ``exc_info=True``.
     *
     * The 404-on-missing-profile-id path is shared with the rest of the
     * CRUD surface via :func:`_find_profile_or_404`.
     * @returns ProfileTestResponse Successful Response
     * @throws ApiError
     */
    public static testProfileApiProfilesProfileIdTestPost({
        profileId,
    }: {
        profileId: string,
    }): CancelablePromise<ProfileTestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/profiles/{profile_id}/test',
            path: {
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
