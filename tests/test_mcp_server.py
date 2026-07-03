"""Tests for the MCP transport layer (Phase 8): token auth + end-to-end tools.

The end-to-end test drives a real MCP client over Streamable HTTP through
httpx's ASGITransport — no sockets — which exercises the exact production
path: auth middleware -> session manager -> tool -> service -> sqlite.
"""

from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import mcp_server
from api.main import app
from core.db import Base, get_db
from core.models import ApiToken, TrackedProduct, User
from core.tokens import generate_token

import core.models  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_env(monkeypatch):
    """sqlite DB shared by the app *and* the MCP server's own session seam."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("api.mcp_server._open_session", lambda: TestingSessionLocal())
    yield TestingSessionLocal
    app.dependency_overrides.clear()


@pytest.fixture
def token(mcp_env):
    """A user + a valid API token; returns (plaintext, user_id)."""
    db = mcp_env()
    user = User(email="mcp-user@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    plain, digest = generate_token()
    db.add(ApiToken(user_id=user.id, name="test", token_hash=digest))
    db.commit()
    user_id = user.id
    db.close()
    return plain, user_id


PING = {"jsonrpc": "2.0", "method": "ping", "id": 1}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_mcp_requires_token(mcp_env):
    with TestClient(app) as client:
        resp = client.post("/mcp", json=PING, headers=MCP_HEADERS)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_mcp_rejects_bad_token(mcp_env):
    with TestClient(app) as client:
        resp = client.post(
            "/mcp", json=PING,
            headers={**MCP_HEADERS, "Authorization": "Bearer dh_live_nope"},
        )
    assert resp.status_code == 401


def test_mcp_accepts_valid_token(mcp_env, token):
    plain, _ = token
    with TestClient(app) as client:
        resp = client.post(
            "/mcp", json=PING,
            headers={**MCP_HEADERS, "Authorization": f"Bearer {plain}"},
        )
    # Past auth: whatever the protocol says about a bare ping, it isn't a 401.
    assert resp.status_code != 401


@pytest.mark.anyio
async def test_mcp_client_end_to_end(mcp_env, token):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    plain, user_id = token

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    async with mcp_server.mcp_lifespan():
        async with streamablehttp_client(
            "http://testserver/mcp",
            headers={"Authorization": f"Bearer {plain}"},
            httpx_client_factory=factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = {t.name for t in (await session.list_tools()).tools}
                assert tools == {
                    "list_wishlist", "add_tracked_product", "get_best_deal",
                    "get_price_history", "search_deals", "create_alert",
                }

                added = await session.call_tool(
                    "add_tracked_product",
                    {"title": "PS5", "query": "playstation 5", "target_price": 399.99},
                )
                assert not added.isError
                assert "PS5" in added.content[0].text

                listed = await session.call_tool("list_wishlist", {})
                assert not listed.isError
                assert "PS5" in listed.content[0].text

                # unknown item -> clean tool error, not a crash
                bad = await session.call_tool("get_best_deal", {"item_id": "not-a-uuid"})
                assert bad.isError
                assert "not found" in bad.content[0].text

    # the tool wrote through the MCP server's own session seam, scoped to our user
    db = mcp_env()
    row = db.scalar(select(TrackedProduct).where(TrackedProduct.title == "PS5"))
    assert row is not None and row.user_id == user_id
    db.close()
