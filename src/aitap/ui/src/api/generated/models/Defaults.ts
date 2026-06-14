/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Per-process default profile selections for runs and the judge.
 *
 * ``None`` on either field is the documented "no default chosen yet"
 * state. The Settings page surfaces it with a yellow Inventory banner
 * (Decision 1 in ``docs/profiles-design.md``). The same shape is the
 * request body for ``PUT /api/settings/defaults``.
 */
export type Defaults = {
    model_profile_id?: (string | null);
    judge_profile_id?: (string | null);
};

