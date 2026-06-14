/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One template chip row on the Add Profile form.
 *
 * Clicking the chip pre-fills the form's ``base_url`` + ``protocol`` +
 * ``model_id`` from this preset; the user still types a free-text
 * ``label`` and pastes their key. ``name`` is the chip's display
 * label (e.g. ``"DeepSeek"``); it is plain text, not a slug, because
 * presets don't have stable ids — the user can rename or delete them
 * freely via the Manage presets editor.
 */
export type ProfilePreset = {
    name: string;
    base_url: string;
    protocol: 'openai-compat' | 'anthropic';
    model_id: string;
};

