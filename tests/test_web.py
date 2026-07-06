"""Tests for the HTMX web frontend (Phase 6).

Uses the shared `client` fixture (in-memory SQLite + dependency override). We
drive the cookie-auth flow and the HTMX fragment endpoints with TestClient,
which persists cookies across requests like a browser.
"""


def _register(client, email="web@example.com", password="supersecret"):
    return client.post(
        "/register",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_public_pages_render(client):
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200


def test_unauthenticated_dashboard_redirects_to_login(client):
    resp = client.get("/app", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_register_sets_cookie_and_logs_in(client):
    resp = _register(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app"
    assert "access_token" in resp.cookies

    # cookie now lets us reach the dashboard
    dash = client.get("/app")
    assert dash.status_code == 200
    assert "Your wishlist" in dash.text


def test_register_rejects_short_password(client):
    resp = client.post(
        "/register", data={"email": "x@example.com", "password": "short"}
    )
    assert resp.status_code == 400
    assert "at least 8" in resp.text


def test_login_wrong_password_shows_error(client):
    _register(client, email="a@example.com")
    resp = client.post(
        "/login", data={"email": "a@example.com", "password": "wrongpass"}
    )
    assert resp.status_code == 401
    assert "Incorrect email or password" in resp.text


def test_login_success_redirects(client):
    _register(client, email="b@example.com", password="supersecret")
    client.cookies.clear()  # drop the register session, log in fresh
    resp = client.post(
        "/login",
        data={"email": "b@example.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "access_token" in resp.cookies


def test_add_item_returns_fragment_then_appears(client):
    _register(client)
    resp = client.post(
        "/app/items",
        data={"title": "Sony WH-1000XM5", "query": "sony wh-1000xm5", "target_price": "299.99"},
    )
    assert resp.status_code == 200
    # HTMX fragment: just the items table, not a full page
    assert "Sony WH-1000XM5" in resp.text
    assert "<html" not in resp.text.lower()

    # and it shows on the dashboard
    assert "Sony WH-1000XM5" in client.get("/app").text


def test_add_item_enqueues_immediate_refresh(client, monkeypatch):
    _register(client)
    queued = []

    def fake_apply_async(args, ignore_result=True, retry=False):
        queued.append(args[0])

    monkeypatch.setattr("workers.tasks.refresh_product.apply_async", fake_apply_async)

    resp = client.post(
        "/app/items",
        data={"title": "Sony WH-1000XM5", "query": "sony wh-1000xm5"},
    )

    assert resp.status_code == 200
    assert len(queued) == 1


def test_item_detail_and_offers_partial(client):
    _register(client)
    client.post("/app/items", data={"title": "Thing", "query": "thing"})
    # find the item's id from the dashboard link
    dash = client.get("/app").text
    import re
    item_id = re.search(r"/app/items/([0-9a-f-]{36})", dash).group(1)

    detail = client.get(f"/app/items/{item_id}")
    assert detail.status_code == 200
    assert "Live offers" in detail.text

    # offers partial renders (no offers yet -> empty state), as a fragment
    offers = client.get(f"/app/items/{item_id}/offers")
    assert offers.status_code == 200
    assert "<html" not in offers.text.lower()


def test_cannot_open_another_users_item(client):
    _register(client, email="owner@example.com")
    client.post("/app/items", data={"title": "Secret", "query": "secret"})
    item_id = __import__("re").search(
        r"/app/items/([0-9a-f-]{36})", client.get("/app").text
    ).group(1)

    # second user
    client.cookies.clear()
    _register(client, email="intruder@example.com")
    resp = client.get(f"/app/items/{item_id}")
    assert resp.status_code == 404


def test_ops_dashboard_requires_login(client):
    resp = client.get("/app/ops", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_ops_dashboard_shows_pipeline_visibility(client):
    _register(client)
    client.post(
        "/app/items",
        data={"title": "Sony WH-1000XM5", "query": "sony wh-1000xm5"},
    )

    resp = client.get("/app/ops")

    assert resp.status_code == 200
    assert "Pipeline dashboard" in resp.text
    assert "Active sources" in resp.text
    assert "Refresh cadence" in resp.text
    assert "Tracked items" in resp.text
    assert "Sony WH-1000XM5" in resp.text
    assert "deal_fetch_success_total" in resp.text
    assert "Recent offers" in resp.text
    assert "Dead letters" in resp.text
    assert 'hx-trigger="every 5s"' in resp.text


def test_seed_demo_items_adds_mock_store_friendly_queries(client, monkeypatch):
    _register(client)
    queued = []

    def fake_apply_async(args, ignore_result=True, retry=False):
        queued.append(args[0])

    monkeypatch.setattr("workers.tasks.refresh_product.apply_async", fake_apply_async)

    resp = client.post("/app/ops/seed-demo", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/ops"
    dash = client.get("/app").text
    assert "Sony WH-1000XM5" in dash
    assert "Nintendo Switch OLED" in dash
    assert "Instant Pot Duo" in dash
    assert len(queued) == 3


def test_refresh_all_enqueues_each_active_item(client, monkeypatch):
    _register(client)
    client.post("/app/items", data={"title": "Thing One", "query": "thing one"})
    client.post("/app/items", data={"title": "Thing Two", "query": "thing two"})
    queued = []

    def fake_apply_async(args, ignore_result=True, retry=False):
        queued.append(args[0])

    monkeypatch.setattr("workers.tasks.refresh_product.apply_async", fake_apply_async)

    resp = client.post("/app/ops/refresh-all", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/ops"
    assert len(queued) == 2


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


# --- Wishlist polish: pause/resume, target edit, at-a-glance deals -----------
def _first_item_id(client):
    import re

    return re.search(r"/app/items/([0-9a-f-]{36})", client.get("/app").text).group(1)


def test_toggle_pauses_and_resumes(client, monkeypatch):
    _register(client)
    client.post("/app/items", data={"title": "Kettle", "query": "kettle"})
    item_id = _first_item_id(client)

    paused = client.post(f"/app/items/{item_id}/toggle")
    assert paused.status_code == 200
    assert "Resume" in paused.text
    assert "paused" in paused.text

    queued = []
    monkeypatch.setattr(
        "workers.tasks.refresh_product.apply_async",
        lambda args, **kw: queued.append(args[0]),
    )
    resumed = client.post(f"/app/items/{item_id}/toggle")
    assert "Pause" in resumed.text
    assert queued == [item_id]  # resuming fetches fresh prices immediately


def test_update_target_price_persists_and_clears(client):
    _register(client)
    client.post("/app/items", data={"title": "Kettle", "query": "kettle"})
    item_id = _first_item_id(client)

    resp = client.post(
        f"/app/items/{item_id}/target",
        data={"target_price": "49.99"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/app/items/{item_id}"
    assert 'value="49.99"' in client.get(f"/app/items/{item_id}").text

    client.post(f"/app/items/{item_id}/target", data={"target_price": ""})
    assert 'value="49.99"' not in client.get(f"/app/items/{item_id}").text


def test_dashboard_rows_show_best_price_and_verdict(client):
    import uuid as uuid_mod
    from datetime import datetime, timezone

    from api.main import app as fastapi_app
    from core.db import get_db as get_db_dep
    from core.models import Offer

    _register(client)
    client.post(
        "/app/items", data={"title": "Kettle", "query": "kettle", "target_price": "60"}
    )
    item_id = _first_item_id(client)

    # no offers yet -> the row says we're still checking
    assert "checking" in client.get("/app").text

    gen = fastapi_app.dependency_overrides[get_db_dep]()
    db = next(gen)
    db.add(
        Offer(
            tracked_product_id=uuid_mod.UUID(item_id),
            source="mock",
            source_product_id="k-1",
            title="Kettle",
            url="https://example.com/kettle",
            last_price=39.99,
            last_seen_at=datetime.now(timezone.utc),
            is_available=True,
        )
    )
    db.commit()
    gen.close()

    dash = client.get("/app").text
    assert "$39.99" in dash
    assert "below target" in dash  # 39.99 <= the $60 target


# --- Track by URL (the "web" source) -----------------------------------------
def _fake_web_offer(url="https://shop.example.com/p/kettle"):
    from decimal import Decimal

    from sources.base import NormalizedOffer

    return NormalizedOffer(
        source="web", source_product_id=url, title="Kettle 9000",
        price=Decimal("59.99"), currency="USD", url=url, available=True,
        image_url="https://img.example.com/kettle.jpg",
    )


def test_track_url_creates_item_with_first_price(client, monkeypatch):
    _register(client)
    monkeypatch.setattr(
        "sources.webpage.WebPageSource.fetch", lambda self, url: _fake_web_offer(url)
    )
    resp = client.post(
        "/app/items/track-url",
        data={"url": "https://shop.example.com/p/kettle", "target_price": "70"},
    )
    assert resp.status_code == 200
    assert "Now tracking" in resp.text
    assert 'hx-swap-oob' in resp.text  # wishlist refreshes out-of-band

    dash = client.get("/app").text
    assert "Kettle 9000" in dash
    assert "$59.99" in dash  # first price point landed immediately
    assert "below target" in dash  # 59.99 <= the $70 target
    assert 'src="https://img.example.com/kettle.jpg"' in dash  # product image shows


def test_track_url_is_idempotent_per_link(client, monkeypatch):
    _register(client)
    monkeypatch.setattr(
        "sources.webpage.WebPageSource.fetch", lambda self, url: _fake_web_offer(url)
    )
    client.post("/app/items/track-url", data={"url": "https://shop.example.com/p/kettle"})
    again = client.post(
        "/app/items/track-url", data={"url": "https://shop.example.com/p/kettle"}
    )
    assert "already tracking" in again.text
    import re

    distinct = set(re.findall(r"/app/items/([0-9a-f-]{36})", client.get("/app").text))
    assert len(distinct) == 1


def test_track_url_surfaces_scrape_failure_in_fragment(client, monkeypatch):
    _register(client)

    def boom(self, url):
        raise ValueError("no structured price data")

    monkeypatch.setattr("sources.webpage.WebPageSource.fetch", boom)
    resp = client.post(
        "/app/items/track-url", data={"url": "https://shop.example.com/p/blog-post"}
    )
    assert resp.status_code == 200  # error rides the fragment, HTMX-style
    assert "Couldn&#39;t find a price" in resp.text or "Couldn't find a price" in resp.text


# --- Marketplace: search sources, one-click track ----------------------------
def test_marketplace_requires_login(client):
    resp = client.get("/app/marketplace", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_marketplace_page_renders(client):
    _register(client)
    resp = client.get("/app/marketplace")
    assert resp.status_code == 200
    assert "Browse deals" in resp.text
    assert 'hx-post="/app/marketplace/search"' in resp.text


def test_marketplace_search_lists_results_cheapest_first(client, monkeypatch):
    _register(client)
    monkeypatch.setattr(
        "api.mcp_service.svc_search_deals",
        lambda query: {
            "results": [
                {"source": "ebay", "source_product_id": "e1", "title": "Kettle Pro",
                 "price": 49.99, "currency": "USD", "url": "https://x/e1", "available": True},
                {"source": "mock", "source_product_id": "m1", "title": "Kettle",
                 "price": 39.99, "currency": "USD", "url": "https://x/m1", "available": True},
            ],
            "failed_sources": ["bestbuy"],
        },
    )
    resp = client.post("/app/marketplace/search", data={"query": "kettle"})
    assert resp.status_code == 200
    assert "<html" not in resp.text.lower()  # fragment, not a page
    assert resp.text.index("$39.99") < resp.text.index("$49.99")  # sorted ascending
    assert "cheapest" in resp.text
    assert "bestbuy" in resp.text  # failed source is disclosed, not hidden


def test_marketplace_track_creates_item_once(client, monkeypatch):
    _register(client)
    queued = []
    monkeypatch.setattr(
        "workers.tasks.refresh_product.apply_async",
        lambda args, **kw: queued.append(args[0]),
    )

    resp = client.post(
        "/app/marketplace/track", data={"title": "Kettle Pro", "query": "kettle"}
    )
    assert resp.status_code == 200
    assert "Tracking" in resp.text
    assert len(queued) == 1
    assert "Kettle Pro" in client.get("/app").text

    # idempotent: tracking the same query again links to the existing item
    again = client.post(
        "/app/marketplace/track", data={"title": "Kettle Pro", "query": "kettle"}
    )
    assert again.status_code == 200
    assert len(queued) == 1  # no second refresh enqueued
    import re

    distinct = set(re.findall(r"/app/items/([0-9a-f-]{36})", client.get("/app").text))
    assert len(distinct) == 1


def test_item_detail_uses_sse_stream(client):
    _register(client, email="sse-ui@example.com")
    client.post("/app/items", data={"title": "Camera", "query": "camera"})
    dash = client.get("/app")
    marker = 'href="/app/items/'
    start = dash.text.index(marker) + len('href="')
    item_url = dash.text[start : dash.text.index('"', start)]

    page = client.get(item_url)
    assert 'hx-ext="sse"' in page.text
    assert f'sse-connect="{item_url}/stream"' in page.text
    assert 'sse-swap="offers"' in page.text
    assert "every 5s" not in page.text  # polling is gone
