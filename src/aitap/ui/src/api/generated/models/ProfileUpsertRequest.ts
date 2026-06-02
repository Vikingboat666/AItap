/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for ``POST /api/profiles`` and ``PUT /api/profiles/{id}``.
 *
 * The ``label`` is free-text + user-editable; the route slugifies it
 * into the ``id`` at creation time. On PUT, the ``id`` is fixed —
 * relabelling a profile does NOT change its id (per the design doc),
 * so the keyring entry and any cross-references stay stable.
 *
 * ``api_key`` is request-only and optional:
 *
 * - On POST: when present, the route immediately calls
 * :func:`aitap.secrets.set_key_for_profile` with the new id; absent
 * means "create the profile with no key yet, the user will add one
 * later".
 * - On PUT: when present, the route updates the keyring entry under
 * the (unchanged) id; absent means "leave the existing key alone".
 *
 * ``use_fallback`` is the explicit opt-in to write the key into
 * ``~/.aitap/secrets.yaml`` when the OS keyring is unusable. The route
 * returns 409 + a plain-language detail when the keyring is down and
 * this flag is false, so the UI can show a confirm dialog and re-POST
 * with ``use_fallback=True``.
 */
export type ProfileUpsertRequest = {
    api_key?: (string | null);
    base_url: string;
    label: string;
    model_id: string;
    notes?: string;
    protocol: 'openai-compat' | 'anthropic';
    use_fallback?: boolean;
};

