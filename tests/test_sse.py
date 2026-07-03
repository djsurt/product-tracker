"""Tests for the SSE live-price stream (Phase 9).

fakeredis's FakeServer is shared between the endpoint's async subscriber and
the test's sync publisher, so a publish from the test surfaces as an SSE
frame — the full worker->browser path minus the real network.

The streaming test drives the ASGI app manually (scope/receive/send): this
starlette version's TestClient buffers the whole response, so `client.stream`
never yields for an endless SSE body.
"""

import asyncio

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from core.db import Base, get_db
from core.events import price_update_channel
from core.models import TrackedProduct

import core.models  # noqa: F401


@pytest.fixture
def sse_env(monkeypatch):
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
    monkeypatch.setattr("api.routers.web._session", lambda: TestingSessionLocal())

    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "api.routers.web._aioredis",
        lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    publisher = fakeredis.FakeRedis(server=server, decode_responses=True)

    with TestClient(app) as client:
        yield client, TestingSessionLocal, publisher
    app.dependency_overrides.clear()


def _register_and_add_item(client, session_factory, email="sse@example.com"):
    client.post(
        "/register", data={"email": email, "password": "supersecret"},
        follow_redirects=False,
    )
    client.post("/app/items", data={"title": "Camera", "query": "camera"})
    db = session_factory()
    item = db.scalar(select(TrackedProduct).where(TrackedProduct.title == "Camera"))
    item_id = item.id
    db.close()
    return item_id


def test_stream_requires_auth(sse_env):
    client, session_factory, _ = sse_env
    item_id = _register_and_add_item(client, session_factory)
    client.cookies.clear()
    resp = client.get(f"/app/items/{item_id}/stream", follow_redirects=False)
    assert resp.status_code == 303


def test_stream_rejects_non_owner(sse_env):
    client, session_factory, _ = sse_env
    item_id = _register_and_add_item(client, session_factory)
    client.cookies.clear()
    client.post(
        "/register", data={"email": "other@example.com", "password": "supersecret"},
        follow_redirects=False,
    )
    resp = client.get(f"/app/items/{item_id}/stream", follow_redirects=False)
    assert resp.status_code == 404


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stream_sends_initial_then_published_updates(sse_env):
    client, session_factory, publisher = sse_env
    item_id = _register_and_add_item(client, session_factory)
    cookie = client.cookies.get("access_token")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/app/items/{item_id}/stream",
        "raw_path": f"/app/items/{item_id}/stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"access_token={cookie}".encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        await sent.put(message)

    task = asyncio.create_task(app(scope, receive, send))
    try:
        start = await asyncio.wait_for(sent.get(), timeout=5)
        assert start["type"] == "http.response.start"
        assert start["status"] == 200
        headers = dict(start["headers"])
        assert headers[b"content-type"].startswith(b"text/event-stream")

        async def read_offers_frame() -> str:
            """Next `event: offers` frame, skipping keep-alive comment frames."""
            buf = ""
            while True:
                while "\n\n" not in buf:
                    msg = await asyncio.wait_for(sent.get(), timeout=20)
                    buf += msg.get("body", b"").decode()
                frame, _, buf = buf.partition("\n\n")
                if frame.startswith("event: offers"):
                    return frame

        first = await read_offers_frame()
        assert "Best deal" in first  # the rendered _offers.html fragment

        # a worker recording a price publishes -> a second frame arrives
        publisher.publish(price_update_channel(str(item_id)), str(item_id))
        second = await read_offers_frame()
        assert "Best deal" in second
    finally:
        disconnect.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass
