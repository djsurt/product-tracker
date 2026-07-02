# Add from Screenshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload a product screenshot → Claude vision identifies it → prefilled wishlist entry the user confirms → tracked by the existing pipeline.

**Architecture:** A new `core/vision.py` module wraps the Anthropic SDK (`claude-haiku-4-5`, structured output via `messages.parse`). Two thin endpoints call it: `POST /wishlist/identify` (JSON API) and `POST /app/items/identify` (HTMX fragment). Item creation reuses the existing `POST /wishlist` / `POST /app/items` paths unchanged — no new DB tables, migrations, or Celery tasks.

**Tech Stack:** FastAPI, Pydantic v2, `anthropic` SDK (new dep), Jinja2 + HTMX, pytest with in-memory SQLite (`tests/conftest.py` `client` fixture).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-screenshot-item-tracking-design.md`
- Python interpreter for all commands: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python` (worktree has no own venv)
- Model id exactly `claude-haiku-4-5`; new dependency `anthropic>=0.92`
- Allowed upload types: `image/png`, `image/jpeg`, `image/webp`, `image/gif`; max 5 MB (5 * 1024 * 1024 bytes)
- Title clamp: 255 chars (DB column limit)
- No live Anthropic API calls in tests — always stub/monkeypatch
- Full suite must stay green: 79 tests passing at baseline

---

### Task 1: Vision module (`core/vision.py`) + settings + dependency

**Files:**
- Modify: `requirements.txt` (add anthropic)
- Modify: `core/settings.py` (2 new fields after the RapidAPI block, ~line 47)
- Modify: `.env.example` (document the new var; file is at repo root)
- Create: `core/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Produces: `core.vision.identify_product(image_bytes: bytes, media_type: str, client: anthropic.Anthropic | None = None) -> ProductIdentification`
- Produces: `core.vision.ProductIdentification` (Pydantic: `identified: bool`, `title: str = ""`, `query: str = ""`, `brand: str | None = None`, `confidence: Literal["high","medium","low"] = "low"`)
- Produces: exceptions `VisionNotConfigured`, `VisionUnavailable`; constants `ALLOWED_IMAGE_TYPES: frozenset[str]`, `MAX_IMAGE_BYTES: int`
- Consumes: `core.settings.get_settings()` — new fields `anthropic_api_key: str | None`, `anthropic_vision_model: str`

- [ ] **Step 1: Add the dependency and install it**

Append to `requirements.txt`:

```
# Screenshot identification (Phase 7): Claude vision turns a product screenshot
# into a title + search query that feeds the existing text-search pipeline.
anthropic>=0.92
```

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/pip install -r requirements.txt`
Expected: `anthropic` installs without dependency conflicts.

- [ ] **Step 2: Add settings fields**

In `core/settings.py`, after the RapidAPI block (after `rapidapi_country: str = "us"`, line 46), insert:

```python
    # --- Screenshot identification (Phase 7) ---
    # Claude vision key; the identify endpoints return 503 until it's set.
    anthropic_api_key: str | None = None
    anthropic_vision_model: str = "claude-haiku-4-5"
```

Add to `.env.example` (near the other source keys):

```
# Claude API key for screenshot → product identification (optional)
ANTHROPIC_API_KEY=
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_vision.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_vision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.vision'` (or ImportError).

- [ ] **Step 5: Implement `core/vision.py`**

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_vision.py -v`
Expected: 5 passed.

- [ ] **Step 7: Run the full suite**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest -q`
Expected: 84 passed (79 baseline + 5 new).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt core/settings.py core/vision.py tests/test_vision.py .env.example
git commit -m "Add Claude-vision product identifier (core/vision.py)"
```

---

### Task 2: JSON API endpoint `POST /wishlist/identify`

**Files:**
- Modify: `api/routers/wishlist.py` (new endpoint; add imports)
- Test: `tests/test_identify_api.py`

**Interfaces:**
- Consumes: `core.vision` — `identify_product`, `ProductIdentification`, `VisionNotConfigured`, `VisionUnavailable`, `ALLOWED_IMAGE_TYPES`, `MAX_IMAGE_BYTES` (Task 1 signatures)
- Produces: `POST /wishlist/identify` (auth, multipart field `file`) → 200 `ProductIdentification` JSON | 415 | 413 | 422 | 502 | 503
- Note for tests: the router must call `vision.identify_product(...)` **via the module** (`from core import vision`) so `monkeypatch.setattr(vision, "identify_product", ...)` intercepts it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identify_api.py`:

```python
"""Endpoint tests for POST /wishlist/identify.

identify_product is monkeypatched at the module attribute the router calls
(core.vision.identify_product) — no Anthropic traffic.
"""

import pytest

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_identify_api.py -v`
Expected: FAIL — 404s (route doesn't exist yet); the auth test may already pass.

- [ ] **Step 3: Implement the endpoint**

In `api/routers/wishlist.py` add imports:

```python
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from core import vision
```

(extend the existing `fastapi` import line rather than duplicating it). Then add after `create_item` (~line 77):

```python
@router.post("/identify", response_model=vision.ProductIdentification)
def identify_item(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> vision.ProductIdentification:
    """Turn an uploaded screenshot into a prefilled wishlist suggestion.

    Read-only: nothing is created here. The client reviews the identification
    and then POSTs /wishlist as usual — one flow for manual and screenshot adds.
    """
    if file.content_type not in vision.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload a PNG, JPEG, WebP, or GIF image",
        )
    # Read one byte past the cap: full read of a huge file wastes memory, and
    # we only need to know whether it exceeds the limit.
    data = file.file.read(vision.MAX_IMAGE_BYTES + 1)
    if len(data) > vision.MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image exceeds 5 MB — crop or resize it",
        )

    try:
        ident = vision.identify_product(data, file.content_type)
    except vision.VisionNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Screenshot identification is not configured",
        )
    except vision.VisionUnavailable:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Identification service failed — try again or add manually",
        )

    if not ident.identified:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Couldn't identify a product in the image — add it manually",
        )
    return ident
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_identify_api.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest -q`
Expected: 91 passed.

```bash
git add api/routers/wishlist.py tests/test_identify_api.py
git commit -m "Add POST /wishlist/identify: screenshot -> prefilled suggestion"
```

---

### Task 3: Web UI — upload form + prefilled-confirm fragment

**Files:**
- Modify: `api/routers/web.py` (new endpoint after `create_item`, ~line 390)
- Create: `api/templates/_identify_result.html`
- Modify: `api/templates/dashboard.html` (add upload card)
- Test: append to `tests/test_web.py`

**Interfaces:**
- Consumes: `core.vision` (same surface as Task 2); existing `require_web_user`, `templates` from `api/routers/web.py`
- Produces: `POST /app/items/identify` (cookie auth, multipart field `file`) → always 200 HTML fragment; on success the fragment contains a form prefilled with `ident.title` / `ident.query` posting to the existing `POST /app/items`; on failure it contains `<p class="error">…` and an empty manual form.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
# --- Add from screenshot (Phase 7) ------------------------------------------
from core import vision as vision_mod
from core.vision import ProductIdentification


def _post_screenshot(client, content_type="image/png", data=b"\x89PNG..."):
    return client.post(
        "/app/items/identify",
        files={"file": ("shot.png", data, content_type)},
    )


def test_identify_fragment_prefills_form(client, monkeypatch):
    _register(client)
    monkeypatch.setattr(
        vision_mod, "identify_product",
        lambda b, mt: ProductIdentification(
            identified=True, title="Sony WH-1000XM5", query="sony wh-1000xm5",
            brand="Sony", confidence="high",
        ),
    )
    resp = _post_screenshot(client)
    assert resp.status_code == 200
    assert 'value="Sony WH-1000XM5"' in resp.text
    assert 'value="sony wh-1000xm5"' in resp.text
    assert 'hx-post="/app/items"' in resp.text  # confirm posts to existing path


def test_identify_fragment_shows_error_on_failure(client, monkeypatch):
    _register(client)

    def boom(b, mt):
        raise vision_mod.VisionUnavailable("down")

    monkeypatch.setattr(vision_mod, "identify_product", boom)
    resp = _post_screenshot(client)
    assert resp.status_code == 200  # HTMX only swaps 2xx; error rides the fragment
    assert "error" in resp.text


def test_identify_fragment_rejects_bad_type(client):
    _register(client)
    resp = _post_screenshot(client, content_type="text/plain")
    assert resp.status_code == 200
    assert "PNG" in resp.text  # helpful message names the allowed formats


def test_identify_requires_login(client):
    resp = client.post(
        "/app/items/identify",
        files={"file": ("shot.png", b"\x89PNG...", "image/png")},
        follow_redirects=False,
    )
    # 303 to /login, like every other /app route (see require_web_user)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_screenshot_flow_creates_tracked_item(client, monkeypatch):
    """Confirming the prefilled form goes through the normal add-item path."""
    _register(client)
    monkeypatch.setattr(
        vision_mod, "identify_product",
        lambda b, mt: ProductIdentification(identified=True, title="T", query="q"),
    )
    assert _post_screenshot(client).status_code == 200
    resp = client.post("/app/items", data={"title": "T", "query": "q", "target_price": ""})
    assert resp.status_code == 200
    assert "T" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_web.py -v -k identify or screenshot`
(quote it: `-k "identify or screenshot"`)
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Create the fragment template**

Create `api/templates/_identify_result.html`:

```html
{# Result of a screenshot identification: either a prefilled confirm form or an
   error + empty manual form. Both post to the existing /app/items endpoint so
   screenshot-adds and manual adds share one code path. #}
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form hx-post="/app/items" hx-target="#items" hx-swap="innerHTML"
      hx-on::after-request="if(event.detail.successful) this.reset()">
  <div class="row" style="align-items:flex-end;">
    <div class="grow">
      <label for="id-title">Title</label>
      <input id="id-title" name="title" value="{{ ident.title if ident else '' }}" required>
    </div>
    <div class="grow">
      <label for="id-query">Search query</label>
      <input id="id-query" name="query" value="{{ ident.query if ident else '' }}" required>
    </div>
    <div style="flex:0 0 130px;">
      <label for="id-target">Target price</label>
      <input id="id-target" name="target_price" type="number" step="0.01" min="0" placeholder="299.99">
    </div>
    <div>
      <button type="submit">Track it</button>
    </div>
  </div>
  {% if ident and ident.brand %}<p class="muted">Identified: {{ ident.brand }} · confidence {{ ident.confidence }}</p>{% endif %}
</form>
```

- [ ] **Step 4: Add the upload card to the dashboard**

In `api/templates/dashboard.html`, after the "Track a new product" card's closing `</div>` (line 31), insert:

```html
<div class="card">
  <h2>Add from screenshot</h2>
  <p class="muted">Upload a screenshot of a product — we'll identify it and prefill the form.</p>
  <form hx-post="/app/items/identify" hx-target="#identify-result" hx-swap="innerHTML"
        hx-encoding="multipart/form-data">
    <div class="row" style="align-items:flex-end;">
      <div class="grow">
        <input type="file" name="file" accept="image/png,image/jpeg,image/webp,image/gif" required>
      </div>
      <div>
        <button type="submit">
          <span class="htmx-indicator" aria-hidden="true">⏳</span>
          Identify
        </button>
      </div>
    </div>
  </form>
  <div id="identify-result"></div>
</div>
```

- [ ] **Step 5: Implement the web endpoint**

In `api/routers/web.py`, add imports at the top (`from fastapi import File, UploadFile` extends the existing import line; plus `from core import vision`). Then after `create_item` (~line 390):

```python
@router.post("/app/items/identify", response_class=HTMLResponse)
def identify_screenshot(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_web_user),
):
    """Screenshot → prefilled add-item form (HTMX fragment).

    Always 200: HTMX only swaps 2xx responses, so errors ride inside the
    fragment as a message plus an empty manual form.
    """
    ident = None
    error = None
    if file.content_type not in vision.ALLOWED_IMAGE_TYPES:
        error = "That file type isn't supported — upload a PNG, JPEG, WebP, or GIF."
    else:
        data = file.file.read(vision.MAX_IMAGE_BYTES + 1)
        if len(data) > vision.MAX_IMAGE_BYTES:
            error = "Image is over 5 MB — crop or resize it and try again."
        else:
            try:
                ident = vision.identify_product(data, file.content_type)
            except vision.VisionNotConfigured:
                error = "Screenshot identification isn't configured on this server."
            except vision.VisionUnavailable:
                error = "Identification failed — try again, or add the item manually."
            else:
                if not ident.identified:
                    ident = None
                    error = "Couldn't spot a product in that image — fill the form manually."
    return templates.TemplateResponse(
        request, "_identify_result.html", {"ident": ident, "error": error}
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest tests/test_web.py -v -k "identify or screenshot"`
Expected: 5 passed.

- [ ] **Step 7: Run the full suite and commit**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest -q`
Expected: 96 passed.

```bash
git add api/routers/web.py api/templates/_identify_result.html api/templates/dashboard.html tests/test_web.py
git commit -m "Add screenshot upload UI: identify -> prefilled confirm form"
```

---

### Task 4: README note

**Files:**
- Modify: `README.md` (build-phases list + a short feature note)

**Interfaces:** none (docs only).

- [ ] **Step 1: Document the feature**

In `README.md`, append to the "Build phases" list:

```markdown
- **Phase 7 — Add from screenshot** ✅ upload a product screenshot; Claude vision (`claude-haiku-4-5`) identifies it into a prefilled wishlist entry (config-gated by `ANTHROPIC_API_KEY`)
```

- [ ] **Step 2: Verify suite still green and commit**

Run: `/Users/dhananjaysurti/personal/asset-generator/.venv/bin/python -m pytest -q`
Expected: 96 passed.

```bash
git add README.md
git commit -m "Document Phase 7: add-from-screenshot"
```
