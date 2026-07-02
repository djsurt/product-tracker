# Add from Screenshot — Design

**Date:** 2026-07-01
**Status:** Approved decisions from user: (1) end result is one tracked product, (2) identification via Claude vision API. Sync-vs-queue question defaulted to inline (user AFK; identification is interactive UX, unlike background price refreshes).

## Goal

A user uploads a screenshot of a product (from any site, app, or photo). The app
identifies the item, prefills a wishlist entry (title + search query), the user
confirms or tweaks it, and from then on it is a normal tracked product — the
existing Celery pipeline finds offers across eBay/RapidAPI and tracks prices.
"Track similar items" is delivered by the existing multi-source offer discovery:
one identified product yields offers from every configured source.

## Non-goals

- No image storage: the screenshot is processed in-memory and discarded.
- No visual-similarity search or embeddings; identification produces a *text*
  query that feeds the existing text-search pipeline.
- No new Celery tasks, DB tables, or migrations.

## Architecture

```
Browser (dashboard "Add from screenshot" form)
   └─ POST /app/items/identify  (multipart image, HTMX)
         └─ core/vision.identify_product(image_bytes, media_type)
               └─ Claude API (claude-haiku-4-5, structured output)
         └─ returns prefilled add-item form partial
   user edits/confirms → POST /app/items  (existing path, unchanged)
         └─ existing enqueue_product_refresh → sources → offers/price_history
```

A JSON variant `POST /wishlist/identify` returns the identification payload for
API clients; the client then POSTs `/wishlist` as usual.

## Components

### 1. Settings (`core/settings.py`)

- `anthropic_api_key: str | None = None` — feature is config-gated like the
  eBay/RapidAPI adapters; when unset, identify endpoints return 503.
- `anthropic_vision_model: str = "claude-haiku-4-5"`.

### 2. Vision module (`core/vision.py`)

- `ProductIdentification` (Pydantic): `identified: bool`, `title: str`,
  `query: str`, `brand: str | None`, `confidence: "high" | "medium" | "low"`.
  When the image contains no recognizable product, the model sets
  `identified=False` (fields empty).
- `identify_product(image_bytes: bytes, media_type: str) -> ProductIdentification`
  - Official `anthropic` SDK (new dependency in requirements.txt),
    `client.messages.parse()` with `output_format=ProductIdentification` —
    guarantees schema-valid output, no hand-rolled JSON parsing.
  - Base64 image content block + short instruction prompt: identify the single
    main product; produce a concise retail search query (brand + model, no
    marketing fluff); title clamped to 255 chars (DB column limit).
  - `max_tokens` small (short structured output).
- Errors: `VisionNotConfigured` (no API key), `VisionUnavailable` (Anthropic
  API/network error — wraps the SDK's typed exceptions). Callers map these to
  HTTP responses; a vision failure never crashes anything else.
- Client is constructed per call with the configured key (cheap; matches how
  other adapters read settings) and is injectable for tests.

### 3. Upload validation (shared by both endpoints)

- Allowed media types: `image/png`, `image/jpeg`, `image/webp`, `image/gif`
  (the set the Claude API accepts) → else 415.
- Max size 5 MB (Claude API per-image limit) → else 413 with a friendly
  "crop or resize" message. Read the upload fully, check `len(bytes)`.

### 4. API endpoint (`api/routers/wishlist.py`)

`POST /wishlist/identify` (auth required, multipart `file`):
- 200 → `ProductIdentificationOut` (schema in `api/schemas.py`).
- 422 when `identified=False` ("couldn't identify a product — add it manually").
- 415/413 on validation failure, 503 when unconfigured, 502 on upstream error.
- Does **not** create the item; the client confirms via existing `POST /wishlist`.

### 5. Web UI (`api/routers/web.py` + templates)

- Dashboard gains an "Add from screenshot" file input next to the existing
  add-item form (HTMX `hx-post`, `hx-encoding="multipart/form-data"`, spinner
  while identifying — the call takes ~2–4 s).
- `POST /app/items/identify` returns a partial: the add-item form prefilled
  with the identified title + query (both editable) and empty target price;
  submitting it posts to the existing `POST /app/items`. On failure it returns
  the same fragment with an error message and empty fields so the user can
  type manually.

## Error handling summary

| Condition | API | Web |
|---|---|---|
| No `anthropic_api_key` | 503 | fragment: "identification not configured" |
| Bad content type | 415 | fragment with message |
| > 5 MB | 413 | fragment: "image too large — crop/resize" |
| Anthropic error/timeout | 502 | fragment: "identification failed, try again or add manually" |
| No product recognized | 422 | fragment with empty editable form + message |

## Testing

- `tests/test_vision.py`: unit tests for `identify_product` with the Anthropic
  client stubbed (parse returns canned `ProductIdentification`; SDK errors →
  `VisionUnavailable`; missing key → `VisionNotConfigured`). No live API calls.
- Endpoint tests (API + web) with `identify_product` monkeypatched: happy path
  (prefilled form / JSON payload), each error row above, auth required.
- Existing suite must stay green; CI unchanged (sqlite/fakeredis).

## New dependency

- `anthropic>=0.92` in requirements.txt (official SDK; used only by
  `core/vision.py`).
