"""Endpoint tests for POST /wishlist/identify.

identify_product is monkeypatched at the module attribute the router calls
(core.vision.identify_product) — no Anthropic traffic.
"""

from core import vision
from core.vision import ProductIdentification

IDENT = ProductIdentification(
    identified=True, title="Sony WH-1000XM5", query="sony wh-1000xm5",
    brand="Sony", confidence="high",
)


def _post_image(client, headers, data=b"\x89PNG...", content_type="image/png"):
    return client.post(
        "/wishlist/identify",
        headers=headers,
        files={"file": ("shot.png", data, content_type)},
    )


def test_identify_requires_auth(client):
    resp = _post_image(client, headers={})
    assert resp.status_code == 401


def test_identify_happy_path(client, auth_headers, monkeypatch):
    monkeypatch.setattr(vision, "identify_product", lambda b, mt: IDENT)
    resp = _post_image(client, auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "sony wh-1000xm5"
    assert body["identified"] is True


def test_identify_rejects_unsupported_type(client, auth_headers):
    resp = _post_image(client, auth_headers, content_type="application/pdf")
    assert resp.status_code == 415


def test_identify_rejects_oversized_image(client, auth_headers):
    big = b"x" * (vision.MAX_IMAGE_BYTES + 1)
    resp = _post_image(client, auth_headers, data=big)
    assert resp.status_code == 413


def test_identify_unrecognized_product_is_422(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        vision, "identify_product",
        lambda b, mt: ProductIdentification(identified=False),
    )
    resp = _post_image(client, auth_headers)
    assert resp.status_code == 422


def test_identify_not_configured_is_503(client, auth_headers, monkeypatch):
    def boom(b, mt):
        raise vision.VisionNotConfigured()

    monkeypatch.setattr(vision, "identify_product", boom)
    assert _post_image(client, auth_headers).status_code == 503


def test_identify_upstream_error_is_502(client, auth_headers, monkeypatch):
    def boom(b, mt):
        raise vision.VisionUnavailable("api down")

    monkeypatch.setattr(vision, "identify_product", boom)
    assert _post_image(client, auth_headers).status_code == 502
