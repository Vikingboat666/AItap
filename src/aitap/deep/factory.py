"""Profile-keyed LLM client factory.

The multi-provider redesign (``docs/profiles-design.md`` §"Backend
architecture / LLM client construction") routes every LLM call through
a per-profile client built by this factory. The route layer does the
secret resolution (``secrets.get_key_for_profile``) and hands the raw
key to :func:`get_client_for_profile`; the factory dispatches on
``profile.protocol`` to a concrete :class:`LLMClient` subclass.

This module deliberately does NOT import :mod:`aitap.secrets`. Keeping
the secret-store boundary at the route layer (not the factory) lets
the AST-discipline test in ``test_secrets_import_discipline.py``
keep the smallest possible allow-list — only the route file that
actually calls ``get_key_for_profile`` ends up on it. The factory is
trivially mockable in unit tests because the only inputs are a
``Profile`` value object and a string.

The factory is a separate module rather than an addition to
``aitap.deep.client`` (the contract file) on purpose: the contract
file holds the :class:`LLMClient` ABC + ``ChatMessage`` / ``ChatResponse``
types that downstream worktrees pin against, and we don't want a
profile-keyed dispatch helper bumping its contract version. Treat
``factory.py`` as the per-redesign glue.
"""

from __future__ import annotations

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.client import LLMClient
from aitap.deep.openai_client import OpenAICompatClient
from aitap.server.routes import Profile


def get_client_for_profile(profile: Profile, api_key: str) -> LLMClient:
    """Build a concrete :class:`LLMClient` for *profile*.

    Args:
        profile: the user-configured endpoint to talk to. The factory
            reads ``protocol``, ``base_url``, and ``model_id`` off the
            value and ignores the key-status fields — those are derived
            metadata, not configuration.
        api_key: the raw key the route layer already resolved from
            :func:`aitap.secrets.get_key_for_profile`. Must be a
            non-empty string; the caller is expected to short-circuit
            with a plain-language "no key set" response before reaching
            this function (see the test endpoint in
            ``server/routes/profiles.py``).

    Dispatch table:

    - ``protocol == "anthropic"`` → :class:`AnthropicClient` with the
      profile's ``base_url`` (so a private gateway works).
    - ``protocol == "openai-compat"`` (default for every other vendor)
      → :class:`OpenAICompatClient` with mandatory ``base_url``.

    Returns:
        An :class:`LLMClient` ready to be ``await``-ed. The factory
        does no network work; the SDK is only imported when the first
        ``chat`` / ``estimate_cost`` call lands on the returned client.
    """
    if profile.protocol == "anthropic":
        return AnthropicClient(
            model=profile.model_id,
            api_key=api_key,
            base_url=profile.base_url,
        )
    # The only other documented protocol is "openai-compat"; the
    # Profile.protocol Literal already constrains the input, so we
    # don't bother with an else / raise — pyright catches a future
    # unhandled arm at the call site.
    return OpenAICompatClient(
        base_url=profile.base_url,
        model=profile.model_id,
        api_key=api_key,
    )


__all__ = ["get_client_for_profile"]
