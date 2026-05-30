/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for ``POST /api/settings/key``.
 *
 * The raw ``key`` is request-only — it is **never** echoed on the
 * response, never logged, and never persisted into the SQLite store.
 * The response is a :class:`ProviderKeyStatus` containing only
 * metadata.
 */
export type SetKeyRequest = {
    key: string;
    provider: 'anthropic' | 'openai';
    use_fallback?: boolean;
};

