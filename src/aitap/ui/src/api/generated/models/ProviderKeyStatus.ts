/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Per-provider API-key state for the Settings page.
 *
 * Additive (CONTRACTS.md): new type, not a rename. The frontend reads
 * this off ``SettingsResponse.keys``; the raw key value never appears
 * on any response, only the masked preview.
 */
export type ProviderKeyStatus = {
    configured: boolean;
    masked?: (string | null);
    provider: 'anthropic' | 'openai';
    source: 'keyring' | 'fallback' | 'env' | 'none';
};

