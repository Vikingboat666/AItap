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

from typing import TYPE_CHECKING, Literal

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.client import LLMClient
from aitap.deep.openai_client import OpenAICompatClient

if TYPE_CHECKING:
    from aitap.config import ProfileConfig
    from aitap.server.routes import Profile


def _dispatch_client(
    *,
    protocol: Literal["openai-compat", "anthropic"],
    model_id: str,
    base_url: str,
    api_key: str,
) -> LLMClient:
    """Shared protocol → :class:`LLMClient` dispatch.

    Pulled out so the two public wrappers below (one for the API-facing
    ``Profile``, one for the config-facing ``ProfileConfig``) share
    identical wiring. Adding a new protocol arm here updates both
    surfaces at once.
    """
    if protocol == "anthropic":
        return AnthropicClient(
            model=model_id,
            api_key=api_key,
            base_url=base_url,
        )
    # The only other documented protocol is "openai-compat"; the
    # input Literal already constrains the value, so we don't bother
    # with an else / raise — pyright catches a future unhandled arm
    # at the call site.
    return OpenAICompatClient(
        base_url=base_url,
        model=model_id,
        api_key=api_key,
    )


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
    return _dispatch_client(
        protocol=profile.protocol,
        model_id=profile.model_id,
        base_url=profile.base_url,
        api_key=api_key,
    )


def get_client_for_profile_config(profile: ProfileConfig, api_key: str) -> LLMClient:
    """Build an :class:`LLMClient` from a config-layer
    :class:`~aitap.config.ProfileConfig`.

    Same dispatch contract as :func:`get_client_for_profile`, but
    accepts the persistent shape (no derived ``key_configured`` /
    ``key_source`` fields). The CLI path uses this so
    ``aitap scan --deep --profile <id>`` doesn't have to construct an
    API-facing :class:`Profile` from a config-facing
    :class:`ProfileConfig` just to round-trip through the factory.

    Layering note: this is the deliberate ``deep`` → ``config``
    direction; ``config`` never imports ``deep``. The TYPE_CHECKING
    import keeps the runtime cost zero for the no-profile call paths.
    """
    return _dispatch_client(
        protocol=profile.protocol,
        model_id=profile.model_id,
        base_url=profile.base_url,
        api_key=api_key,
    )


__all__ = ["get_client_for_profile", "get_client_for_profile_config"]
