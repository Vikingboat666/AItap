"""Profile-keyed image-client factory.

Wave 5 Part B (``docs/wave-5-design.md`` §"Part B" / §"B·Decision 1")
ships the image grid as a parallel surface to the chat layer: every
image-generation call goes through a per-profile :class:`ImageClient`
built by this factory. The route layer (future ``image-dispatch``
worktree) does the secret resolution
(:func:`aitap.secrets.get_key_for_profile`) and hands the raw key to
:func:`get_image_client_for_profile`; the factory dispatches on
``profile.protocol`` to a concrete subclass.

This module deliberately does NOT import :mod:`aitap.secrets`. Keeping
the secret-store boundary at the route layer (not the factory) lets
the AST-discipline test in ``test_secrets_import_discipline.py`` keep
the smallest possible allow-list — only the route file that actually
calls ``get_key_for_profile`` ends up on it. The factory is trivially
mockable in unit tests because the only inputs are a ``Profile`` value
object and a string.

The factory mirrors :func:`aitap.deep.factory.get_client_for_profile`
but raises :class:`aitap.images.client.ImageProviderError` for the
``anthropic`` protocol: Anthropic does not ship an image-generation
endpoint (as of LAST_UPDATED in :mod:`aitap.images.pricing`), and
silently routing an Anthropic profile to a stub would either burn a
chat call's worth of cost or fail with a misleading 404. A loud,
plain-language refusal at construction time is the safer default.
"""

from __future__ import annotations

from aitap.images.client import ImageClient, ImageProviderError
from aitap.images.openai_client import OpenAIImageClient
from aitap.server.routes import Profile


def get_image_client_for_profile(profile: Profile, api_key: str) -> ImageClient:
    """Build a concrete :class:`ImageClient` for *profile*.

    Args:
        profile: the user-configured endpoint to talk to. The factory
            reads ``protocol``, ``base_url``, and ``model_id`` off the
            value and ignores the key-status fields — those are derived
            metadata, not configuration.
        api_key: the raw key the route layer already resolved from
            :func:`aitap.secrets.get_key_for_profile`. Must be a
            non-empty string; the caller is expected to short-circuit
            with a plain-language "no key set" response before reaching
            this function (mirrors the chat-side factory contract).

    Dispatch table:

    - ``protocol == "openai-compat"`` → :class:`OpenAIImageClient` with
      the profile's ``base_url``.
    - ``protocol == "anthropic"`` → :class:`ImageProviderError`.
      Anthropic doesn't offer a text-to-image endpoint; the explicit
      refusal keeps the UI honest about which profiles can drive the
      image grid.

    Returns:
        An :class:`ImageClient` ready to be ``await``-ed. The factory
        does no network work; the SDK is only imported when the first
        ``generate`` / ``estimate_cost`` call lands on the returned
        client.

    Raises:
        ImageProviderError: when ``profile.protocol`` is ``"anthropic"``
            (Anthropic has no image API) or when ``api_key`` is empty
            (the route layer should have caught it earlier — the guard
            is defensive).
    """
    if not api_key:
        # The chat-side factory's contract assumes the route layer
        # already filtered no-key profiles; we enforce the same here so
        # a buggy caller can't construct a client that would 401 on
        # every call.
        raise ImageProviderError(
            "Image profile has no API key configured. Open Settings to add one."
        )

    if profile.protocol == "anthropic":
        raise ImageProviderError(
            "Anthropic profiles cannot generate images. "
            "Use an OpenAI-compatible profile (DALL-E) for image generation."
        )

    # The only other documented protocol is "openai-compat" — the
    # Profile.protocol Literal already constrains the input, so a
    # future arm would surface as a pyright error at the call site.
    return OpenAIImageClient(
        base_url=profile.base_url,
        model=profile.model_id,
        api_key=api_key,
    )


__all__ = ["get_image_client_for_profile"]
