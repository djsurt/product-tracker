# Design: Remote MCP Server + SSE Live Prices

**Date:** 2026-07-02
**Status:** Approved
**Goal:** Two portfolio-flagship features on top of the existing deployment:
a remote MCP endpoint so any MCP client (Claude Code / Claude Desktop) can
manage a wishlist conversationally against the live site, and Server-Sent
Events so prices on the item page update the instant a worker records them.

Built as two independent phases, each separately demo-able and deployable:

- **Phase A — MCP server** (API tokens → tools → dashboard UI → tests)
- **Phase B — SSE live prices** (worker publish → stream endpoint → HTMX SSE)

---

## Phase A — Remote MCP server (`/mcp`)

### What it is

The live AWS deployment exposes an MCP Streamable-HTTP endpoint at `/mcp`.
A user creates a personal API token in the dashboard, then connects with:

```bash
claude mcp add --transport http deal-hunter \
  https://deal-hunter.100-56-44-101.sslip.io/mcp \
  --header "Authorization: Bearer dh_live_..."
```

Demo line: *"Claude, is my PS5 at a 30-day low? If not, alert me at $399."*

### Approach

Official `mcp` Python SDK (FastMCP) with Streamable HTTP, **mounted into the
existing FastAPI app** — same container, same Caddy, zero new infra.

Alternatives considered and rejected:

- `fastapi-mcp` (auto-converts REST endpoints to tools): fast but produces
  verbose auto-generated tools; deliberately designed tools are the better
  engineering story.
- Local stdio server: no auth work, but loses the "connect to my live site"
  demo entirely.
- OAuth (the MCP-spec-blessed auth flow, required by claude.ai web
  connectors): explicitly **out of scope** — bearer-header auth works with
  Claude Code and Claude Desktop and keeps the surface small. YAGNI.

### Auth: per-user API tokens

- New table `api_tokens`: `id (uuid pk)`, `user_id (fk users, cascade
  delete)`, `name (text)`, `token_hash (text, unique)`, `created_at`,
  `last_used_at (nullable)`. Alembic migration.
- Token format `dh_live_<32+ random url-safe chars>`, generated server-side,
  **shown once** at creation, stored only as a SHA-256 hash (GitHub PAT
  pattern). Lookup = hash the presented token, select by `token_hash`,
  update `last_used_at`.
- Dashboard gets an **"API access"** section: create token (named), list
  tokens (name + created + last used, never the value), revoke (delete).
  Cookie-auth like the rest of `/app`.
- Every MCP request carries `Authorization: Bearer dh_live_…`. The MCP
  layer resolves token → `User` before any tool runs; missing/invalid/
  revoked tokens get a clean 401. All tool queries are scoped to that user,
  reusing the existing ownership-gate pattern from `api/routers/wishlist.py`
  (`_get_owned`) — no IDOR regression.

### Tools (6, deliberately curated)

| Tool | Signature | Backed by |
|---|---|---|
| `list_wishlist` | `()` | existing list query; includes current best price per item |
| `add_tracked_product` | `(name, query, target_price?)` | existing create path + `enqueue_product_refresh` |
| `get_best_deal` | `(item_id)` | existing cache-aside best-deal path incl. 30-day-low verdict |
| `get_price_history` | `(item_id, limit?)` | existing price-history query (best offer's history) |
| `search_deals` | `(query)` | live fan-out over enabled source adapters (eBay, RapidAPI) |
| `create_alert` | `(item_id, rule, threshold?)` | existing alert create incl. its validation rules |

Implementation notes:

- Source adapters are sync (`httpx` sync); `search_deals` runs them via
  `anyio.to_thread` so the ASGI event loop never blocks. Per-source failures
  degrade gracefully (skip the failing source, report the rest).
- Tools return compact JSON-friendly dicts (ids as strings, prices as
  floats + currency) — sized for an LLM context, not a UI.
- Tool errors are structured MCP errors (`item not found`, `invalid rule`),
  never stack traces.
- DB sessions: each tool call opens/closes its own session (no request-scoped
  dependency injection inside the mounted MCP app).

### Testing

- Unit: token generate/hash/verify round-trip; revoked + malformed token
  rejected.
- Per-tool tests using the MCP SDK's in-memory client against
  sqlite/fakeredis (matches existing CI harness).
- Auth-scoping test: user A's token cannot read or mutate user B's items.
- Manual live smoke test after deploy: `claude mcp add …` against prod.

---

## Phase B — SSE live prices

### What it is

The item detail page currently polls (`hx-trigger="load, every 5s"`).
Replace with Server-Sent Events so the offers table updates the moment a
worker records a new price — visibly live, backed by Redis pub/sub.

### Design

- **Publish (worker):** in `workers/tasks.py`, right where a fetched price
  is recorded (next to the existing `price_dropped` event emit), publish
  `{"tracked_product_id": ...}` to Redis channel
  `price_updates:{tracked_product_id}`.
- **Stream (API):** new async endpoint `GET /app/items/{item_id}/stream`,
  cookie auth + ownership gate (reuse `require_web_user` + owned-item
  check). Subscribes via `redis.asyncio` pub/sub; on each message, renders
  the existing `_offers.html` fragment and emits it as an SSE `offers`
  event. Sends a heartbeat comment every 15s so proxies don't reap idle
  connections. Caddy passes SSE through without config changes.
- **Frontend:** HTMX SSE extension (vendored js, consistent with how htmx
  itself is served):
  `hx-ext="sse" sse-connect="/app/items/{id}/stream" sse-swap="offers"`
  replacing the polling trigger on the offers container. The motion
  system's "no entrance replay on refresh" rule carries over unchanged —
  it's the same fragment swap.
- **Fallback:** page still renders full data on initial load; on SSE error
  the extension auto-reconnects. Worst case equals today's behavior minus
  auto-refresh. Ops dashboard keeps polling (out of scope).

### Testing

- Pipeline test: recording a price publishes to the right channel.
- Endpoint test: a published message yields a rendered SSE frame with the
  offers fragment; unauthenticated / non-owner requests are rejected.
- Manual live check on AWS after deploy (two browser windows: trigger a
  refresh, watch the other window tick).

---

## Error handling summary

- MCP: bad token → 401; unknown item → structured "not found" tool error;
  source adapter failure inside `search_deals` → partial results with the
  failing source omitted.
- SSE: Redis unavailable → stream endpoint returns 503 (page still works
  statically); dropped connection → client auto-reconnect.

## Out of scope

- OAuth for MCP / claude.ai web connectors.
- SSE for the dashboard list page and ops dashboard.
- Token scopes/permissions (all tokens are full-access for their user).
- Rate limiting on `/mcp` beyond what already exists app-wide.
