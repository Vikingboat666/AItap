/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Stop conditions for :func:`iterate_loop` — Decision 3 defaults.
 *
 * All three primary rules are **relative** (delta-from-baseline,
 * round-over-round stagnation, hard cap on rounds). ``absolute_threshold``
 * exists as the one legitimate use of an absolute score gate (e.g.
 * safety must reach 0.95 regardless of baseline) but is ``None`` by
 * default so judge-prompt drift and task heterogeneity don't make stops
 * fragile.
 */
export type ConvergenceConfig = {
    max_rounds?: number;
    delta_from_baseline?: number;
    stagnation_window?: number;
    stagnation_epsilon?: number;
    absolute_threshold?: (number | null);
};

