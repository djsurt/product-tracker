"""Unit tests for the Claude-vision product identifier.

The Anthropic client is always stubbed — no network, no key needed. The stub
mirrors the one SDK surface we use: `client.messages.parse(...).parsed_output`.
"""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from core import vision
from core.settings import get_settings
from core.vision import ProductIdentification, identify_product

PNG = b"\x89PNG fake image bytes"


def stub_client(result=None, error=None):
    def parse(**kwargs):
        if error is not None:
            raise error
        return SimpleNamespace(parsed_output=result)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_identify_product_returns_identification():
    ident = ProductIdentification(
        identified=True, title="Sony WH-1000XM5", query="sony wh-1000xm5",
        brand="Sony", confidence="high",
    )
    out = identify_product(PNG, "image/png", client=stub_client(result=ident))
    assert out.identified is True
    assert out.query == "sony wh-1000xm5"


def test_identify_product_clamps_title_to_255():
    ident = ProductIdentification(identified=True, title="x" * 300, query="q")
    out = identify_product(PNG, "image/png", client=stub_client(result=ident))
    assert len(out.title) == 255


def test_identify_product_sends_base64_image_block():
    captured = {}
    ident = ProductIdentification(identified=True, title="t", query="q")

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(parsed_output=ident)

    client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    identify_product(PNG, "image/png", client=client)
    block = captured["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert captured["model"] == get_settings().anthropic_vision_model


def test_api_error_becomes_vision_unavailable():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=req)
    with pytest.raises(vision.VisionUnavailable):
        identify_product(PNG, "image/png", client=stub_client(error=err))


def test_missing_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    with pytest.raises(vision.VisionNotConfigured):
        identify_product(PNG, "image/png")  # no injected client → needs a key
