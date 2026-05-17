/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Provider } from './Provider';
import type { ProviderEvidence } from './ProviderEvidence';
export type SettingsResponse = {
    cost_per_run_usd: number;
    cost_per_session_usd: number;
    judge_model: (string | null);
    model: string;
    provider: Provider;
    providers_available: Array<ProviderEvidence>;
};

