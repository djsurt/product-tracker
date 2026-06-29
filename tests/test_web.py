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
