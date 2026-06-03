"""OpenAIImageClient tests using SDK mocks — no network.

Mirrors the OpenAICompatClient tests on the chat side: the SDK is
mocked via ``sys.modules`` so a real network round-trip never happens,
and the fake records the kwargs the SDK constructor + the
``images.generate`` resource were called with so the dispatch contract
is observable.

Coverage targets:

- Construction stores ``base_url`` / ``api_key`` / ``model``.
- ``generate`` reaches ``client.images.generate(...)`` with the right
  kwargs and decodes the base64 payload into raw bytes.
- ``AuthenticationError`` / ``RateLimitError`` / ``APIError`` map to
  the matching :class:`ImageProviderError` subclasses.
- **PR #35 B2 anti-leak**: the SDK exception body never reaches the
  ``ImageProviderError`` message. Static plain-language detail strings
  only.
"""

from __future__ import annotations

import base64
import sys
import types
from typing import Any

import pytest

from aitap.images.client import (
    ImageProviderAuthError,
    ImageProviderError,
    ImageProviderRateLimitError,
)
from aitap.images.openai_client import OpenAIImageClient

# --------------------------------------------------------------------------- #
# SDK fakes — same shape as deep.openai_client tests                          #
# --------------------------------------------------------------------------- #


class _FakeImageRecord:
    def __init__(self, b64_json: str) -> None:
        self.b64_json = b64_json


class _FakeImagesResponse:
    def __init__(self, records: list[_FakeImageRecord]) -> None:
        self.data = records


class _ImagesResource:
    def __init__(self, response: _FakeImagesResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> _FakeImagesResponse:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeOpenAIClient:
    def __init__(self, response: _FakeImagesResponse | Exception) -> None:
        self.images = _ImagesResource(response)


class FakeAuthError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPIError(Exception):
    pass


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeImagesResponse | Exception,
) -> _FakeOpenAIClient:
    """Install a fake ``openai`` SDK module that records constructor kwargs."""
    fake_client = _FakeOpenAIClient(response)

    def _AsyncOpenAI(*, base_url: str, api_key: str) -> _FakeOpenAIClient:
        fake_client._used_base_url = base_url  # type: ignore[attr-defined]
        fake_client._used_api_key = api_key  # type: ignore[attr-defined]
        return fake_client

    fake_module = types.SimpleNamespace(
        AsyncOpenAI=_AsyncOpenAI,
        AuthenticationError=FakeAuthError,
        RateLimitError=FakeRateLimitError,
        APIError=FakeAPIError,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_client


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


# --------------------------------------------------------------------------- #
# Construction                                                                #
# --------------------------------------------------------------------------- #


def test_construction_records_base_url_and_key() -> None:
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE-image",
    )
    assert client.base_url == "https://api.openai.com/v1"
    assert client.model == "dall-e-3"
    assert client.api_key == "sk-FAKE-image"
    assert client.provider_name == "openai"


def test_construction_rejects_empty_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        OpenAIImageClient(base_url="", model="dall-e-3", api_key="sk-x")


def test_construction_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAIImageClient(base_url="https://api.openai.com/v1", model="dall-e-3", api_key="")


def test_construction_does_not_touch_network() -> None:
    """Pure construction — no SDK import."""
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-2",
        api_key="sk-FAKE",
    )
    assert client.base_url == "https://api.openai.com/v1"


# --------------------------------------------------------------------------- #
# generate() — happy path                                                     #
# --------------------------------------------------------------------------- #


async def test_generate_passes_base_url_and_api_key_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mandatory ``base_url`` reaches the SDK constructor verbatim."""
    fake = _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(b"\x89PNG\r\n\x1a\n"))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE-image",
    )
    await client.generate("a cat astronaut", size="1024x1024", quality="standard", n=1)

    assert fake._used_base_url == "https://api.openai.com/v1"  # type: ignore[attr-defined]
    assert fake._used_api_key == "sk-FAKE-image"  # type: ignore[attr-defined]


async def test_generate_sends_documented_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(b"png-bytes"))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    await client.generate("a frog", size="1024x1024", quality="hd", n=2)

    call = fake.images.calls[0]
    assert call["model"] == "dall-e-3"
    assert call["prompt"] == "a frog"
    assert call["n"] == 2
    assert call["size"] == "1024x1024"
    assert call["quality"] == "hd"
    # response_format pinned to b64_json so the decoded bytes flow back
    # without a second URL fetch (Decision 3 single-trip).
    assert call["response_format"] == "b64_json"


async def test_generate_drops_quality_for_dall_e_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DALL-E 2 rejects ``quality="hd"`` at the wire level on recent
    OpenAI SDK builds (N2 follow-up). The client drops the field when
    the model name starts with ``dall-e-2`` so a HD-by-default UI picker
    can't trigger a confusing 400 the anti-leak guard would re-map to
    a generic 'retry' detail.
    """
    fake = _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(b"png"))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-2",
        api_key="sk-FAKE",
    )
    await client.generate("a cat", size="1024x1024", quality="hd", n=1)

    call = fake.images.calls[0]
    assert call["model"] == "dall-e-2"
    assert "quality" not in call, (
        "DALL-E 2 must not receive a ``quality`` field — the SDK rejects "
        "``quality=hd`` against dall-e-2 with a 400."
    )


async def test_generate_validates_kwargs_before_touching_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1 follow-up: ImageGenerationRequest invariants (non-empty prompt,
    1 <= n <= 10) hold on the kwargs path too. An empty prompt raises
    ValueError before the SDK is even imported.
    """
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ValueError, match="prompt cannot be empty"):
        await client.generate("", size="1024x1024", quality="standard", n=1)
    with pytest.raises(ValueError, match=r"n must be <= 10"):
        await client.generate("a cat", size="1024x1024", quality="standard", n=11)


async def test_generate_decodes_base64_into_raw_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x89PNG\r\n\x1a\nfakepayload"
    _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(payload))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    result = await client.generate("a frog", size="1024x1024", quality="standard", n=1)
    assert len(result.images) == 1
    assert result.images[0].bytes == payload
    # The size literal is parsed into (width, height) ints so the
    # downstream UI can lay out the grid without re-parsing.
    assert result.images[0].width == 1024
    assert result.images[0].height == 1024


async def test_generate_returns_usage_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(b"a")), _FakeImageRecord(_b64(b"b"))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    result = await client.generate("frog", size="1024x1024", quality="standard", n=2)
    assert result.usage.images_generated == 2
    # 2 images at $0.040 each (dall-e-3 standard 1024x1024 row).
    assert result.cost_usd == pytest.approx(0.080)


async def test_generate_forwards_seed_into_each_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provided seed is echoed on every returned GeneratedImage so
    the grid view can label the column."""
    _install_fake_sdk(
        monkeypatch,
        _FakeImagesResponse([_FakeImageRecord(_b64(b"png"))]),
    )
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    result = await client.generate("frog", size="1024x1024", quality="standard", n=1, seed=42)
    assert result.images[0].seed == 42


# --------------------------------------------------------------------------- #
# generate() — error mapping                                                  #
# --------------------------------------------------------------------------- #


async def test_generate_wraps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAuthError("bad key 12345"))
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ImageProviderAuthError) as exc_info:
        await client.generate("frog", size="1024x1024", quality="standard", n=1)
    # PR #35 B2: the SDK body must not appear in the user-facing message.
    assert "bad key 12345" not in str(exc_info.value)
    assert "Settings" in str(exc_info.value)


async def test_generate_wraps_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeRateLimitError("slow down 429"))
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ImageProviderRateLimitError) as exc_info:
        await client.generate("frog", size="1024x1024", quality="standard", n=1)
    assert "slow down 429" not in str(exc_info.value)
    assert "rate-limited" in str(exc_info.value)


async def test_generate_wraps_generic_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAPIError("server overloaded internal-trace-9k"))
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ImageProviderError) as exc_info:
        await client.generate("frog", size="1024x1024", quality="standard", n=1)
    # PR #35 B2: SDK body must not leak. The detail names the next
    # action (retry / check status page) rather than echoing the body.
    assert "server overloaded internal-trace-9k" not in str(exc_info.value)
    assert "Retry" in str(exc_info.value) or "retry" in str(exc_info.value)


async def test_generate_raises_when_sdk_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy-import discipline: a missing ``openai`` extra raises a
    plain-language ``ImageProviderError`` rather than a bare ImportError."""
    monkeypatch.setitem(sys.modules, "openai", None)
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ImageProviderError, match="OpenAI SDK"):
        await client.generate("frog", size="1024x1024", quality="standard", n=1)


# --------------------------------------------------------------------------- #
# Anti-leak canary: no SDK exception body in the error message                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc_class",
    [FakeAuthError, FakeRateLimitError, FakeAPIError],
)
async def test_sdk_exception_body_never_leaks_into_error_message(
    monkeypatch: pytest.MonkeyPatch, exc_class: type[Exception]
) -> None:
    """PR #35 B2 anti-leak canary: a unique sentinel embedded in the
    SDK exception body must not appear in the
    :class:`ImageProviderError` ``str`` — the detail is static plain
    language and the technical body stays on ``__cause__``."""
    sentinel = "SENTINEL-CANARY-DO-NOT-LEAK-42"
    _install_fake_sdk(monkeypatch, exc_class(sentinel))
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    with pytest.raises(ImageProviderError) as exc_info:
        await client.generate("frog", size="1024x1024", quality="standard", n=1)
    assert sentinel not in str(exc_info.value)
    # The original SDK exception is still on ``__cause__`` so a
    # maintainer can inspect it without parsing strings.
    assert exc_info.value.__cause__ is not None
    assert sentinel in str(exc_info.value.__cause__)


# --------------------------------------------------------------------------- #
# estimate_cost                                                               #
# --------------------------------------------------------------------------- #


def test_estimate_cost_for_known_model_returns_priced_estimate() -> None:
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="dall-e-3",
        api_key="sk-FAKE",
    )
    estimate = client.estimate_cost("any prompt", size="1024x1024", quality="standard", n=4)
    # 4 images at $0.040 each (dall-e-3 standard 1024x1024 row).
    assert estimate.usd == pytest.approx(0.160)
    assert estimate.n == 4
    assert estimate.model == "dall-e-3"


def test_estimate_cost_for_unknown_model_degrades_to_zero() -> None:
    """Public surface returns 0 USD for unpriced models — the route
    layer renders ``cost: unknown``. Same convention as
    :meth:`OpenAICompatClient.estimate_cost` on the chat side."""
    client = OpenAIImageClient(
        base_url="https://api.openai.com/v1",
        model="midjourney-v9",
        api_key="sk-FAKE",
    )
    estimate = client.estimate_cost("any prompt", size="1024x1024", quality="standard", n=2)
    assert estimate.usd == 0.0
    assert estimate.model == "midjourney-v9"
