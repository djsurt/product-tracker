"""Screenshot → product identification via Claude vision.

One narrow job: given raw image bytes, return a structured guess at what
product is shown — title, retail search query, brand, confidence. The result
feeds the *existing* text-search pipeline; this module never touches the DB
or the queue. Config-gated like the source adapters: no ANTHROPIC_API_KEY,
no feature (callers get VisionNotConfigured and map it to a 503).

Structured output via `messages.parse` means the SDK validates the model's
answer against ProductIdentification — no hand-rolled JSON parsing.
"""

from __future__ import annotations

import base64
from typing import Literal

import anthropic
from pydantic import BaseModel

from core.settings import get_settings

# Claude API image limits: these four media types, 5 MB per image.
ALLOWED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_TITLE_MAX = 255  # tracked_products.title column limit


class VisionNotConfigured(Exception):
    """No API key configured — the feature is off, not broken."""


class VisionUnavailable(Exception):
    """The Claude API call failed (network, rate limit, server error)."""


class ProductIdentification(BaseModel):
    """What the model saw. identified=False means 'no recognizable product'."""

    identified: bool
    title: str = ""
    query: str = ""
    brand: str | None = None
    confidence: Literal["high", "medium", "low"] = "low"


_PROMPT = """You identify retail products from screenshots for a price tracker.

Identify the single main product shown in the image.
- title: short product name a shopper would recognize (brand + model).
- query: concise lowercase retail search query to find this exact product on
  shopping sites (brand + model number if visible; no marketing fluff).
- brand: the brand, if identifiable.
- confidence: how sure you are this is the exact product (high/medium/low).
If the image does not clearly show a product, set identified=false and leave
the other fields empty."""


def identify_product(
    image_bytes: bytes,
    media_type: str,
    client: anthropic.Anthropic | None = None,
) -> ProductIdentification:
    """Ask Claude what product is in the image.

    `client` is injectable for tests; in production we build one per call from
    settings (construction is cheap — no connection until the request).
    """
    if client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise VisionNotConfigured("ANTHROPIC_API_KEY is not set")
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.parse(
            model=get_settings().anthropic_vision_model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
            output_format=ProductIdentification,
        )
    except anthropic.APIError as exc:
        raise VisionUnavailable(str(exc)) from exc

    ident = response.parsed_output
    if ident is None:
        raise VisionUnavailable("model returned no parseable identification")

    ident.title = ident.title.strip()[:_TITLE_MAX]
    ident.query = ident.query.strip()
    return ident
