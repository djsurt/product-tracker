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
