# Deal Hunter

Build a wishlist of products, track their prices across the internet, and get
alerted + redirected when the best deal appears.

This is a learning project: the architecture is intentionally chosen to exercise
core system-design concepts (async job queues, scheduling, caching, time-series
data, event-driven notifications, reliability). See
`~/.claude/plans/i-want-to-build-sequential-quail.md` for the full plan.

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
- **Phase 4** — Notifications + redirect
- **Phase 5** — Scraper adapter + reliability hardening
- **Phase 6** — Frontend polish + cloud deploy

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

## Tests

```bash
pytest
```

## Project layout

```
api/          FastAPI app (routers, app entrypoint)
core/         shared: settings, db/session, ORM models
workers/      Celery app + tasks            (Phase 2+)
sources/      price-source adapters          (Phase 2+)
mock_store/   seeded fake storefront         (Phase 2)
migrations/   Alembic migrations
tests/        test suite
```
