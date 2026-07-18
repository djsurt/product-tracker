# Deal Hunter

Build a wishlist of products, track their prices across the internet, and get
alerted + redirected when the best deal appears.

## Stack

- **API:** FastAPI + SQLAlchemy 2.0 + Alembic
- **Workers / queue:** Celery + Redis *(Phase 2+)*
- **DB:** Postgres 16
- **Cache / broker:** Redis
- **Orchestration:** Docker Compose

## Architecture (target)

```
Frontend → API (FastAPI) → Postgres + Redis
                ↓ enqueue
         Redis broker → Celery workers (source adapters) → Postgres
         Celery Beat (scheduler) ┘        ↓ price-drop event
                                   Notification worker → email
```

The API stays thin and fast; all slow/unreliable price-fetching happens in
background workers pulled from a queue.

## Build phases

Built one phase at a time — each phase is independently runnable and demo-able.

- **Phase 0 — Scaffold** ✅ docker-compose (api/postgres/redis), FastAPI `/health`, Alembic
- **Phase 1 — Domain + auth + wishlist CRUD** ✅ JWT auth, `users` + `tracked_products`, per-user CRUD
- **Phase 2 — Mock store + first adapter + Celery pipeline** ✅ Beat → workers fetch prices into `offers` + `price_history`; prices update on their own
- **Phase 3 — Real sources + caching + best-deal logic** ✅ eBay/Best Buy adapters (config-gated), Redis cache-aside best-deal, lowest-in-30d verdict, rate limiting + idempotency locks + backoff
- **Phase 4 — Notifications + redirect** ✅ alert rules (`below_target`/`pct_drop`), price-drop event → notify worker → email (MailHog), debounce, `/go/{offer_id}` click-logging redirect
- **Phase 5 — Scraper adapter + reliability hardening** ✅ HTML `ScraperSource` (httpx + BeautifulSoup), dead-letter table on exhausted retries, structured logging (structlog), Prometheus `/metrics`, horizontal worker scaling
- **Phase 6 — Frontend + cloud deploy** ✅ server-rendered HTMX UI (`/app`) with live-polling prices + SVG price chart, cookie auth, `render.yaml` deploy blueprint
- **Phase 7 — Add from screenshot** ✅ upload a product screenshot; Claude vision (`claude-haiku-4-5`) identifies it into a prefilled wishlist entry (config-gated by `ANTHROPIC_API_KEY`)
- **Phase 8 — Remote MCP server** ✅ token-authenticated MCP endpoint at `/mcp` (Streamable HTTP); create a token on the dashboard, then let Claude manage your wishlist
- **Phase 9 — SSE live prices** ✅ item page streams price updates over Server-Sent Events (Redis pub/sub → `/app/items/{id}/stream` → htmx SSE extension) instead of polling
- **Phase 10 — 3D/AR product previews** ✅ AI-generated 3D model per item (Meshy image-to-3D from the best offer's photo); interactive `<model-viewer>` + phone AR, async Celery generation with live SSE progress (config-gated by `MESHY_API_KEY`)

## Running locally

```bash
cp .env.example .env

# With Docker (recommended): runs `alembic upgrade head` then starts the API.
docker compose up --build
# API:        http://localhost:8000
# Swagger UI: http://localhost:8000/docs   (use "Authorize" to log in)
# Liveness:   http://localhost:8000/health
# Readiness:  http://localhost:8000/health/ready   (checks Postgres + Redis)

# Without Docker (needs local Postgres + Redis reachable per .env):
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head          # create the schema
uvicorn api.main:app --reload
```

### Try the API (Phase 1)

```bash
# register
curl -X POST localhost:8000/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"password123"}'

# login -> grab access_token (form-encoded; username = email)
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d 'username=me@example.com&password=password123' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# add a wishlist item
curl -X POST localhost:8000/wishlist \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title":"Sony WH-1000XM5","query":"sony wh-1000xm5","target_price":"299.99"}'

# list your wishlist
curl localhost:8000/wishlist -H "authorization: Bearer $TOKEN"
```

Or just open `http://localhost:8000/docs`, click **Authorize**, and use the UI.

## Connecting Claude (MCP)

Create an API token in the dashboard (`/app` → "Connect Claude"), then:

```bash
claude mcp add --transport http deal-hunter https://<your-host>/mcp \
  --header "Authorization: Bearer dh_live_..."
```

Six tools are exposed, all scoped to the token's user: `list_wishlist`,
`add_tracked_product`, `get_best_deal`, `get_price_history`, `search_deals`,
`create_alert`. Tokens are stored as SHA-256 digests and shown exactly once.

### Watch the pipeline (Phase 2)

With the stack up, prices track on their own (Beat sweeps every 30s). To see it
immediately for one item:

```bash
# (TOKEN + an item created as above; ID = its id)
curl -X POST localhost:8000/wishlist/$ID/refresh -H "authorization: Bearer $TOKEN"

# current offers across sources + the best deal among them
curl localhost:8000/wishlist/$ID/offers -H "authorization: Bearer $TOKEN"

# append-only price history for one offer
curl localhost:8000/wishlist/$ID/offers/$OFFER_ID/history -H "authorization: Bearer $TOKEN"
```

The mock storefront is at `http://localhost:9000` (`/search?q=...`). Scale workers
with `docker compose up --scale worker=3`.

### Get alerted on a price drop (Phase 4)

When a tracked offer's price falls, `fetch_offer` emits a `price_dropped` event
(a separate `notify` task), which checks your **alert rules** and emails you —
captured by **MailHog** so nothing real is sent. Open the inbox at
`http://localhost:8025`.

```bash
# create an alert rule on a wishlist item (TOKEN + ID as above):
#   below_target -> fire when the best price <= threshold (or the item's target_price)
curl -X POST localhost:8000/wishlist/$ID/alerts \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"rule":"below_target","threshold":"250.00"}'

#   pct_drop -> fire when an offer falls by >= threshold percent
curl -X POST localhost:8000/wishlist/$ID/alerts \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"rule":"pct_drop","threshold":"10"}'

# refresh a few times until the mock price drifts down, then check MailHog:
#   http://localhost:8025
```

Each email links to `GET /go/{offer_id}` — our redirect. It logs the click
(`click_events`) and 302s to the store, keeping our app the source of truth for
which deals users act on. The same alert won't re-fire within
`ALERT_DEBOUNCE_SECONDS` (default 1h).

### Reliability + observability (Phase 5)

**Scraper source.** A fourth adapter, `ScraperSource`, parses HTML instead of
calling an API (httpx + BeautifulSoup). It scrapes the mock store's own
`/html/...` pages, so you learn fragile DOM parsing against a target you control.
Enable it with `SCRAPER_ENABLED=true`; it then appears in `active_sources`
alongside `mock` and every wishlist item gains scraped offers too. (For a
JS-rendered site you'd swap the fetch for Playwright behind the same interface.)

**Personal marketplace scrapers.** If you don't have marketplace API keys, you
can opt into best-effort eBay and SHEIN HTML scrapers:

```bash
EBAY_SCRAPER_ENABLED=true
SHEIN_SCRAPER_ENABLED=true
```

They appear as `ebay_scraper` and `shein_scraper`. They use public HTML pages,
parse only the fields needed for price tracking, and are intentionally disabled
by default because live marketplace markup and anti-bot behavior can change.

**Delisted offers.** When a source answers 404/410 for a known listing (an
ended eBay auction, a removed product page), that's a permanent fact, not a
transient failure: the adapter raises `ListingGoneError` and the worker marks
the offer `is_delisted` immediately — no retries, no dead-letter — so the sweep
stops re-fetching a listing that can never come back. Its price history stays
for charts, and if a source's search ever returns the same listing id again,
discovery un-delists it.

**Dead-letter queue.** When `fetch_offer` (or `notify`) exhausts its retries, it
doesn't fail forever or vanish — it parks a row in `dead_letters` and marks the
offer stale (`is_available=false`) so its bad price isn't trusted. Inspect with:

```bash
docker compose exec postgres psql -U deals -d deals -c \
  'select task_name, retries, error, created_at from dead_letters order by created_at desc limit 10;'
```

**Structured logs.** Workers and the API log one structured event per line via
structlog (console locally, JSON elsewhere) — `docker compose logs -f worker`
shows `fetch_offer.ok offer_id=… source=… price=…`, so you can trace a price
through the pipeline.

**Metrics.** `GET /metrics` exposes Prometheus counters (fetch successes/retries/
failures, dead-letters, notifications, redirect clicks). They're stored in Redis
so counts aggregate across *all* worker processes, not just one:

```bash
curl localhost:8000/metrics
```

**Horizontal scale.** Workers are stateless; run more of them and the Redis
queue load-balances across them. The per-offer idempotency lock (Phase 3) keeps
two workers from double-writing the same offer:

```bash
docker compose up --scale worker=3
```

### The web UI (Phase 6)

With the stack up, open **`http://localhost:8000/app`** (the JSON API stays at
its routes; the browser UI is a parallel face over the same data). Register,
add a wishlist item, and open it — the offers panel **auto-refreshes every 5s**
via htmx and shows a live best-deal badge and an inline SVG price chart. It's a
server-rendered **HTMX + Jinja** app (no separate frontend build); auth is a
httponly session cookie carrying the same JWT the API uses.

```
/login  /register        auth pages (sets the session cookie)
/app                     your wishlist (add / delete items)
/app/items/{id}          live offers, best deal, price chart, alert rules
```

### 3D/AR previews (Phase 10)

Set `MESHY_API_KEY` (free tier at [meshy.ai](https://www.meshy.ai)) and the
item page gains a **"✨ Generate 3D preview"** button when an offer has a
product photo. Generation runs as a Celery task (Meshy image-to-3D → download
`.glb` + `.usdz`), progress streams over the same SSE connection as prices,
and the interactive viewer swaps in live — on a phone, the AR badge places
the product in your room via Quick Look / Scene Viewer (no app install).

Cost is bounded by design: one cached model per item (regenerate is explicit),
a hard monthly cap (`MODEL3D_MONTHLY_CAP`, default 8), and the whole feature is
dark without the key. Bad photos fail gracefully with a retry. Pre-generate
demo items so nobody waits:

```bash
python -m scripts.pregen_models <tracked_product_id> [...]
```

Files land in `MODEL3D_STORAGE_DIR` (a shared `model3d` volume in prod) and are
served ownership-checked at `/app/models/{item_id}.glb|.usdz`.

### Cloud deploy

`render.yaml` is a [Render](https://render.com) blueprint describing the whole
stack — managed Postgres + Redis and three services (web, worker, beat) off the
single `Dockerfile`, the cloud mirror of `docker-compose.yml`. Connect the repo
in Render (or `render blueprint launch`); it runs `alembic upgrade head` on
deploy. After the first deploy, set `APP_BASE_URL` to the public web URL and add
any `SMTP_*` / source API keys you want live. `JWT_SECRET` is generated for you.
Bare `postgres://` connection strings are auto-upgraded to the psycopg driver
(`core/db.py`), so the managed `DATABASE_URL` works unchanged.

## Tests

```bash
pytest
```

## Project layout

```
api/          FastAPI app (routers, app entrypoint)
api/templates/ Jinja templates for the HTMX UI    (Phase 6)
core/         shared: settings, db/session, ORM models
workers/      Celery app + tasks            (Phase 2+)
sources/      price-source adapters          (Phase 2+)
mock_store/   seeded fake storefront         (Phase 2)
migrations/   Alembic migrations
tests/        test suite
```
