/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConvergenceConfig } from './ConvergenceConfig';
/**
 * Inbound body for ``POST /api/iterate``.
 *
 * ``provider`` / ``model`` selection is intentionally absent — the
 * background task constructs an :class:`LLMClient` via the same
 * factory the playground uses, which already reads project Settings.
 * Tests substitute via :func:`aitap.playground.dispatch.set_client_factory`.
 */
export type IterateSessionRequest = {
    prompt_id: string;
    dataset_id: string;
    mode?: 'auto' | 'guided' | 'manual';
    instruction?: (string | null);
    manual_revisions?: (Record<string, string> | null);
    user_thumbs?: (Record<string, Record<string, 'up' | 'down'>> | null);
    user_notes?: (Record<string, Record<string, string>> | null);
    convergence?: (ConvergenceConfig | null);
};

