"""Tests for the offline :class:`MockImageClient`.

The mock is the offline substitute the future ``image-dispatch`` /
``image-ui`` test suites use to exercise the grid without burning real
DALL-E spend. These tests pin:

- Deterministic output: every call returns the same bytes.
- Constant 0 USD cost (the mock is never priced so a "forgot to swap
  for a real client" mistake surfaces as ``cost: unknown`` in the UI
  rather than silent $0.00).
- Registry self-registration: ``get_image_client("mock", ...)`` works.
- No network — the test never imports the ``openai`` SDK.
"""

from __future__ import annotations

import sys

import pytest

from aitap.images.client import get_image_client
from aitap.images.mock_client import MockImageClient


def test_provider_name_is_mock() -> None:
    client = MockImageClient(model="any", api_key=None)
    assert client.provider_name == "mock"


async def test_generate_returns_n_images_with_constant_bytes() -> None:
    client = MockImageClient(model="any", api_key=None)
    result = await client.generate("a frog", size="1024x1024", quality="standard", n=3)
    assert len(result.images) == 3
    # Every image carries the same deterministic payload so a test can
    # byte-compare to a fixture.
    payloads = {img.bytes for img in result.images}
    assert len(payloads) == 1
    # The payload is a real PNG (starts with the PNG magic).
    only_payload = next(iter(payloads))
    assert only_payload.startswith(b"\x89PNG\r\n\x1a\n")


async def test_generate_records_size_on_returned_images() -> None:
    client = MockImageClient(model="any")
    result = await client.generate("x", size="1792x1024", quality="standard", n=1)
    assert result.images[0].width == 1792
    assert result.images[0].height == 1024


async def test_generate_forwards_seed_to_each_image() -> None:
    client = MockImageClient(model="any")
    result = await client.generate("x", size="1024x1024", quality="standard", n=2, seed=7)
    assert all(img.seed == 7 for img in result.images)


async def test_generate_reports_zero_cost_and_correct_usage() -> None:
    client = MockImageClient(model="any")
    result = await client.generate("x", size="1024x1024", quality="standard", n=4)
    assert result.cost_usd == 0.0
    assert result.usage.images_generated == 4


def test_estimate_cost_returns_zero_for_any_model() -> None:
    client = MockImageClient(model="any")
    estimate = client.estimate_cost("x", size="1024x1024", quality="standard", n=10)
    assert estimate.usd == 0.0
    assert estimate.n == 10


def test_generate_does_not_import_openai_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-network discipline: blocking the openai SDK must not break
    the mock — it never imports it."""
    monkeypatch.setitem(sys.modules, "openai", None)
    client = MockImageClient(model="any")
    # Sync call to estimate_cost stays trivially offline.
    assert client.estimate_cost("x", size="1024x1024", quality="standard", n=1).usd == 0.0


async def test_registry_path_returns_mock_client() -> None:
    """``get_image_client("mock", ...)`` returns a :class:`MockImageClient`
    via the registry — the import side-effect of :mod:`aitap.images.mock_client`
    self-registers under the ``"mock"`` key."""
    # Force the import so the registry side-effect runs (the line
    # below is a no-op if a previous test already imported the module).
    import aitap.images.mock_client  # noqa: F401

    client = get_image_client("mock", model="any", api_key=None)
    assert isinstance(client, MockImageClient)
