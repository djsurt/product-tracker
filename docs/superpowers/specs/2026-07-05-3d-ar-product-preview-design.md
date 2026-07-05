# "View in your space" — AI-generated 3D/AR product previews

**Date:** 2026-07-05
**Status:** Approved (design), pending implementation plan

## Goal

Give every tracked product an optional AI-generated 3D preview: an interactive,
rotatable model on the item page, and — on phones — a native AR view that
places the product in the user's room through the camera. This is the app's
flagship "visible wow" feature: instantly graspable in a 2-minute demo, backed
by a defensible async-pipeline engineering story.

**Success criteria:**
- Tapping "Generate 3D preview" on an item with an offer image produces an
  interactive 3D model on the page within ~2 minutes, with live progress and no
  page refresh.
- On iOS and Android, the ready model offers a working native AR view
  (Quick Look / Scene Viewer) with no app install.
- Total external spend is bounded by a hard monthly cap; the feature is
  entirely dark without an API key (CI, local dev, and fresh clones are
  unaffected).
- Every failure mode (bad photo, provider error, cap reached) renders a
  friendly, on-brand state — never a spinner that hangs or a raw error.

## Non-goals

- No text-to-3D, no user-uploaded photos for generation (the input is always a
  tracked offer's `image_url`).
- No model editing, re-texturing, or quality settings exposed to the user.
- The phase-two polish pass (demo account, onboarding/empty states, OG tags,
  micro-UX sweep) is a separate spec.

## Provider decision

**Meshy image-to-3D API.** Chosen because it exports both formats we need —
`.glb` (web viewer + Android Scene Viewer) and `.usdz` (iOS Quick Look) — from
a single generation job, and has a free tier sized to a portfolio project
(~8 generations/month at defaults). The provider client lives behind a small
interface so Tripo or another vendor could be swapped in later.

**Budget stance: free tier only.** Cost control is a first-class design
constraint, not an afterthought (see Caps below).

## Architecture

### Data model

New table `product_models` — one row per tracked product; the unique FK *is*
the cache-once guarantee:

| column               | type                | notes                                   |
|----------------------|---------------------|-----------------------------------------|
| `id`                 | uuid pk             |                                         |
| `tracked_product_id` | uuid fk, **unique** | cascade delete with the product         |
| `status`             | str                 | `pending → generating → ready \| failed` |
| `provider`           | str                 | `"meshy"`                               |
| `provider_task_id`   | str nullable        | Meshy job id, for polling/debugging     |
| `source_image_url`   | str                 | the photo we sent                       |
| `glb_path`           | str nullable        | relative path under storage dir         |
| `usdz_path`          | str nullable        | relative path under storage dir         |
| `error`              | text nullable       | last failure, for the retry state       |
| `created_at` / `updated_at` | datetime     |                                         |

Model files (5–20MB `.glb`) live on a Docker volume, not in Postgres.

### Configuration (follows the existing config-gating pattern)

- `MESHY_API_KEY` — feature is completely dark unless set (same pattern as
  `ANTHROPIC_API_KEY` for screenshot-identify).
- `MODEL3D_MONTHLY_CAP` — default **8**, sized to Meshy's free tier.
- `MODEL3D_STORAGE_DIR` — default `./model3d` (a compose volume in prod).

### Generation pipeline

New Celery task `generate_model3d(tracked_product_id)`, shaped like
`fetch_offer`:

1. **Trigger:** user taps "Generate 3D preview" → web route checks the cap and
   any existing row, inserts a `pending` row, enqueues the task, and returns
   the progress fragment immediately (htmx swap).
2. **Generate:** the task selects the source photo — the current best-deal
   offer's `image_url`, falling back to any available offer that has one —
   creates a Meshy image-to-3D job, and polls until complete (~60s typical).
3. **Store:** downloads `.glb` + `.usdz` into `MODEL3D_STORAGE_DIR`, marks the
   row `ready`, and publishes a `model3d_updates` event via Redis pub/sub —
   the same mechanism as `price_updates`. The item page's existing SSE stream
   gains a second event type; the viewer swaps in live, no refresh.
4. **Fail:** on exhausted retries the row goes `failed` with a friendly
   message and a `dead_letters` row is parked, consistent with the pipeline.
   A retry re-enqueues (and counts against the cap).

### Serving

`GET /app/models/{product_id}.glb` and `.usdz`, served from the storage volume
with an ownership check (the session user must own the tracked product), same
scoping as every other route.

### Caps & cost control

- **Hard monthly cap:** a Redis counter keyed by calendar month
  (`model3d:count:2026-07`), checked before enqueue, incremented on provider
  job creation. At the cap the button renders the cap-reached state.
- **Cache once:** the unique row per product means a model is never
  regenerated implicitly. Credits are spent only by explicit user action: the
  first generation, a retry after failure, or a small "Regenerate" link on a
  ready model (for when the mesh came out rough).
- **Demo insurance:** `scripts/pregen_models.py` pre-generates models for
  chosen item ids so the demo account always has 2–3 models ready and a
  recruiter never waits or burns the cap.

## UX (item detail page)

One slot next to the product image with five states:

1. **No model yet:** quiet "✨ Generate 3D preview *(beta)*" button. Rendered
   only when the feature is enabled *and* an offer has an `image_url`.
2. **Generating:** warm progress card streamed over SSE — "Sculpting your 3D
   model… usually about a minute." Brand voice: friendly, not terminal-y.
3. **Ready:** inline interactive `<model-viewer>` (Google web component,
   **vendored/self-hosted**, lazy-loaded only on item pages) with slow
   auto-rotate and drag-to-spin. On phones, the "View in your space" AR badge
   opens native Quick Look (iOS, `ios-src` usdz) / Scene Viewer (Android, glb).
   A small "Regenerate" link handles rough meshes (spends a credit, cap
   applies).
4. **Failed:** "We couldn't sculpt this one — some photos don't cooperate.
   Try again?" with a retry button.
5. **Cap reached:** "This month's 3D budget is used up — it resets on the 1st."

Quality variance is designed-in, not hidden: the *(beta)* label, honest
failure copy, and retry. Clean white-background listing photos generate well;
busy or low-res photos may fail or come out rough, and the app stays graceful.

### Accessibility

- `prefers-reduced-motion` disables auto-rotate.
- The viewer has a poster image and a text fallback; all states are
  keyboard-reachable with labeled controls.
- Status changes are announced (the SSE swap targets a region with an
  appropriate live-region role).

## Error handling summary

| failure                        | behavior                                             |
|--------------------------------|------------------------------------------------------|
| No offer image                 | button not rendered                                  |
| Provider 4xx/5xx, timeouts     | Celery retry w/ backoff → `failed` + dead-letter     |
| Generation succeeds, bad mesh  | user sees the model; retry available (spends credit) |
| Cap reached                    | pre-enqueue check → cap-reached state, no spend      |
| Missing file on disk (ready row) | 404 from file route; page falls back to poster     |
| Feature key unset              | zero UI surface anywhere                             |

## Testing

Matching the existing suite's style (sqlite/fakeredis, no network):

- **Provider client:** httpx-mocked Meshy responses — create, poll-pending,
  poll-done, poll-failed, download.
- **Task:** state transitions (`pending→ready`, `pending→failed`→dead-letter),
  idempotency (existing `ready` row short-circuits), cap increment timing.
- **Cap:** enforcement at the boundary (cap-1 allows, cap blocks), month
  rollover key change.
- **Routes:** ownership checks on file serving (404 for another user's model),
  generate endpoint auth + cap + duplicate handling.
- **Templates:** all five UI states render in `tests/test_web.py` style;
  feature-off renders nothing.
- **No real Meshy calls in CI** — the key is never set there.

## Out of scope / future

- Phase-two polish pass (separate spec): one-click demo account, onboarding &
  empty states, favicon/OG tags, loading skeletons, toasts, relative
  timestamps.
- S3/object storage for model files (local volume is fine at this scale).
- Provider fallback chain (Meshy → Tripo) if the free tier proves too tight.
