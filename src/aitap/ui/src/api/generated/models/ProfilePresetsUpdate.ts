/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProfilePreset } from './ProfilePreset';
/**
 * Body for ``PUT /api/profile-presets``.
 *
 * Carries the whole new list — replace-in-full semantics. Per-row
 * add/edit/delete operations happen client-side in the editor;
 * persistence is a single round-trip on Save. Keeps the storage layer
 * a flat JSON file the user can also edit by hand.
 */
export type ProfilePresetsUpdate = {
    presets: Array<ProfilePreset>;
};

