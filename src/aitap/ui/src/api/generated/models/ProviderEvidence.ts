/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CodeLocation } from './CodeLocation';
import type { Provider } from './Provider';
/**
 * What the env scan turned up about configured providers.
 */
export type ProviderEvidence = {
    provider: Provider;
    source: '.env' | 'config' | 'code';
    location: CodeLocation;
    key_var_name: string;
};

