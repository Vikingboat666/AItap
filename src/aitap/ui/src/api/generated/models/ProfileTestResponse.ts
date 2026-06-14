/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Result of ``POST /api/profiles/{profile_id}/test``.
 *
 * Connectivity probe outcome for one profile. ``ok=True`` means the
 * minimal "ping" chat call returned a 2xx; ``ok=False`` reports a
 * coarse reason so the UI can render the right plain-language
 * remediation. ``detail`` is a human sentence (never a stack trace,
 * never the key).
 */
export type ProfileTestResponse = {
    ok: boolean;
    reason?: ('auth' | 'rate_limit' | 'network' | 'other' | null);
    detail?: (string | null);
};

