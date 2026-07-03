"""Remote MCP server (Phase 8), mounted at /mcp on the existing app.

Layers, outside-in:

1. `TokenAuthASGI` — raw ASGI middleware. Resolves `Authorization: Bearer
   dh_live_…` to a user (401 otherwise) and stashes the user id in a
   contextvar for the duration of the request.
2. `_McpEndpoint` — delegates to the StreamableHTTPSessionManager owned by
   the *current* lifespan. A manager can only `.run()` once, and TestClient
   enters the app lifespan once per test, so `mcp_lifespan()` builds a fresh
   manager each time instead of reusing FastMCP's cached one.
3. `@mcp.tool()` wrappers — read the user id, then run the sync service
   function on a worker thread with its own short-lived DB session.

Auth is bearer-token, not OAuth, by design: Claude Code / Desktop connect
with a --header flag; see the spec for the trade-off.
"""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from functools import partial

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from api import mcp_service
from core.models import User
from core.tokens import resolve_token

_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_user_id", default=None
)

mcp = FastMCP("deal-hunter")


# --- session + user plumbing ------------------------------------------------
def _open_session():
    """The module's only DB seam — tests monkeypatch this."""
    from core.db import SessionLocal

    return SessionLocal()


def _require_user_id() -> str:
    uid = _current_user_id.get()
    if uid is None:  # unreachable behind the middleware; guards direct calls
        raise mcp_service.McpServiceError("not authenticated")
    return uid


def _with_user_db(user_id: str, fn, /, *args, **kwargs):
    """Run a service function with a fresh session + the resolved user."""
    db = _open_session()
    try:
        user = db.get(User, uuid.UUID(user_id))
        if user is None:
            raise mcp_service.McpServiceError("not authenticated")
        return fn(db, user, *args, **kwargs)
    finally:
        db.close()


async def _run(fn, *args, **kwargs):
    """Offload a user-scoped sync service call to a worker thread."""
    uid = _require_user_id()  # read the contextvar *before* changing threads
    return await anyio.to_thread.run_sync(partial(_with_user_db, uid, fn, *args, **kwargs))


# --- the 6 tools --------------------------------------------------------------
@mcp.tool()
async def list_wishlist() -> list[dict]:
    """List every product on the user's wishlist, each with its current best
    deal (best price, source, and a verdict like 'all_time_low' or 'fair')."""
    return await _run(mcp_service.svc_list_wishlist)


@mcp.tool()
async def add_tracked_product(
    title: str, query: str, target_price: float | None = None
) -> dict:
    """Start tracking a product. `title` is a display name, `query` is what we
    search stores for (e.g. 'sony wh-1000xm5'), and `target_price` (optional)
    is the price at which the user wants to be alerted."""
    return await _run(mcp_service.svc_add_tracked_product, title, query, target_price)


@mcp.tool()
async def get_best_deal(item_id: str) -> dict:
    """Current best deal for one wishlist item: cheapest live offer, whether
    it's the lowest in the last 30 days, and a buy/wait verdict."""
    return await _run(mcp_service.svc_get_best_deal, item_id)


@mcp.tool()
async def get_price_history(item_id: str, limit: int = 50) -> list[dict]:
    """Recent observed prices (newest first) for the best offer of one
    wishlist item — use this to see the trend before buying."""
    return await _run(mcp_service.svc_get_price_history, item_id, limit)


@mcp.tool()
async def search_deals(query: str) -> dict:
    """Search all enabled stores live for a product and return current offers
    with prices. Sources that fail are listed in `failed_sources`."""
    _require_user_id()  # auth-only: search has no per-user state
    return await anyio.to_thread.run_sync(partial(mcp_service.svc_search_deals, query))


@mcp.tool()
async def create_alert(
    item_id: str, rule: str, threshold: float | None = None
) -> dict:
    """Create a price alert on a wishlist item. rule='below_target' emails
    when the best price drops to `threshold` (or the item's target_price);
    rule='pct_drop' emails on any drop of `threshold` percent."""
    return await _run(mcp_service.svc_create_alert, item_id, rule, threshold)


# --- transport: endpoint + lifespan + auth ------------------------------------
class _McpEndpoint:
    """ASGI endpoint delegating to the manager created by mcp_lifespan()."""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    async def __call__(self, scope, receive, send) -> None:
        if self.manager is None:
            await JSONResponse(
                {"error": "MCP server is not running"}, status_code=503
            )(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)


_endpoint = _McpEndpoint()


@contextlib.asynccontextmanager
async def mcp_lifespan():
    # NOTE: `mcp._mcp_server` is the SDK's low-level server behind FastMCP.
    # It's a private attribute, used deliberately: FastMCP's own
    # streamable_http_app() caches one session manager forever, and a manager
    # can only .run() once per instance — which breaks any process that
    # cycles the app lifespan (every TestClient context). If an SDK upgrade
    # renames it, tests/test_mcp_server.py fails loudly.
    manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server, stateless=True, json_response=True
    )
    _endpoint.manager = manager
    try:
        async with manager.run():
            yield
    finally:
        _endpoint.manager = None


def _resolve_user_id(token: str) -> str | None:
    db = _open_session()
    try:
        user = resolve_token(db, token)
        return str(user.id) if user is not None else None
    finally:
        db.close()


class TokenAuthASGI:
    """Bearer-token gate in front of the MCP endpoint."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = Headers(scope=scope).get("authorization") or ""
        token = auth.removeprefix("Bearer ").strip()
        user_id = await anyio.to_thread.run_sync(partial(_resolve_user_id, token))
        if user_id is None:
            await JSONResponse(
                {"error": "missing or invalid API token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        ctx_token = _current_user_id.set(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user_id.reset(ctx_token)


mcp_asgi_app = TokenAuthASGI(_endpoint)


class McpPathASGI:
    """Dispatches /mcp (with or without trailing slash) ahead of the router.

    A plain `app.mount("/mcp", ...)` 307-redirects the bare `/mcp` POST to
    `/mcp/`, and MCP clients refuse to follow redirects — so we short-circuit
    before Starlette routing instead of mounting.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            await mcp_asgi_app(scope, receive, send)
            return
        await self.app(scope, receive, send)
