/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Result of ``POST /api/settings/test/{provider}``.
 *
 * ``ok=True`` means the minimal probe call (Anthropic ``/v1/messages``
 * or OpenAI ``chat.completions`` with a single ``"ping"`` and
 * ``max_tokens=4``) returned a 2xx. ``ok=False`` reports a coarse
 * reason so the UI can render the right plain-language remediation;
 * ``detail`` is a human sentence (never a stack trace, never the key).
 */
export type TestKeyResponse = {
    detail?: (string | null);
    ok: boolean;
    reason?: ('auth' | 'rate_limit' | 'network' | 'other' | null);
};

