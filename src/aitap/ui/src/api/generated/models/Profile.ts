/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One configured LLM endpoint, exactly as the Settings page renders it.
 *
 * The persistent fields (``id``, ``label``, ``base_url``, ``protocol``,
 * ``model_id``, ``notes``) live in ``.aitap/config.yaml`` under
 * ``profiles:``. The key-status triple (``key_configured``,
 * ``key_source``, ``key_masked``) is *derived* per request from
 * :mod:`aitap.secrets` — the raw key never appears on this model.
 */
export type Profile = {
    id: string;
    label: string;
    base_url: string;
    protocol: 'openai-compat' | 'anthropic';
    model_id: string;
    notes?: string;
    key_configured: boolean;
    key_source: 'keyring' | 'fallback' | 'none';
    key_masked?: (string | null);
};

