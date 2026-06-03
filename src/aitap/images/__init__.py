"""Image-generation client layer for the Wave 5 Part B grid view.

This package mirrors :mod:`aitap.deep` but for text-to-image providers.
The :class:`aitap.images.client.ImageClient` ABC is intentionally parallel
to :class:`aitap.deep.client.LLMClient` rather than an extension of it
(``docs/wave-5-design.md`` §"Part B — B·Decision 1"): image generation
has a different call shape (prompt → N images, size/quality knobs, raw
bytes out) than chat, so bolting ``generate_image`` onto every chat
provider would pollute the chat contract for one new capability.

A separate registry (:data:`aitap.images.client._REGISTRY`) keeps the
two surfaces from colliding — an OpenAI chat provider and an OpenAI
image provider can register under the same provider name without
fighting each other for the slot. Downstream consumers (``image-dispatch``
worktree, future image grid UI) talk to this package via
:func:`aitap.images.factory.get_image_client_for_profile` rather than
the registry directly, matching the per-profile dispatch pattern the
multi-provider redesign settled on for chat.
"""

from __future__ import annotations
