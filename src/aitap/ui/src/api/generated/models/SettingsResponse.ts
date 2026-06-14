/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Defaults } from './Defaults';
import type { Provider } from './Provider';
import type { ProviderEvidence } from './ProviderEvidence';
/**
 * Snapshot of the current process's effective settings.
 *
 * The legacy provider-keyed ``keys`` array (``list[ProviderKeyStatus]``)
 * is removed in contract v3. Per-profile key status now lives inline
 * on each :class:`Profile` returned by ``GET /api/profiles``; clients
 * that need to render key state read that endpoint instead. The
 * legacy provider/model/judge_model fields are retained for
 * backward-compat reading only — the UI no longer surfaces them
 * after the multi-provider redesign.
 */
export type SettingsResponse = {
    provider: Provider;
    model: string;
    judge_model: (string | null);
    cost_per_run_usd: number;
    cost_per_session_usd: number;
    providers_available: Array<ProviderEvidence>;
    defaults?: Defaults;
};

