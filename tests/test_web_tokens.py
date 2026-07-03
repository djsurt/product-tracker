"""Tests for the dashboard API-token management UI (Phase 8)."""

from core.tokens import TOKEN_PREFIX


def _register(client, email="tok@example.com", password="supersecret"):
    return client.post(
        "/register", data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_create_token_shows_plaintext_once(client):
    _register(client)
    resp = client.post("/app/tokens", data={"name": "my-laptop"})
    assert resp.status_code == 200
    assert TOKEN_PREFIX in resp.text          # one-time reveal in the fragment
    assert "my-laptop" in resp.text

    # the dashboard lists the token by name but never its value
    dash = client.get("/app")
    assert "my-laptop" in dash.text
    assert TOKEN_PREFIX not in dash.text


def test_revoke_token(client):
    _register(client)
    client.post("/app/tokens", data={"name": "old-token"})

    # fish the token id out of the re-rendered fragment on the dashboard
    dash = client.get("/app")
    marker = 'hx-delete="/app/tokens/'
    start = dash.text.index(marker) + len(marker)
    token_id = dash.text[start : dash.text.index('"', start)]

    resp = client.delete(f"/app/tokens/{token_id}")
    assert resp.status_code == 200
    assert "old-token" not in client.get("/app").text


def test_tokens_are_user_scoped(client):
    _register(client, email="owner@example.com")
    client.post("/app/tokens", data={"name": "owners-token"})
    dash = client.get("/app")
    marker = 'hx-delete="/app/tokens/'
    start = dash.text.index(marker) + len(marker)
    token_id = dash.text[start : dash.text.index('"', start)]

    client.cookies.clear()
    _register(client, email="intruder@example.com")
    client.delete(f"/app/tokens/{token_id}")  # must be a no-op

    client.cookies.clear()
    client.post(
        "/login", data={"email": "owner@example.com", "password": "supersecret"}
    )
    assert "owners-token" in client.get("/app").text
