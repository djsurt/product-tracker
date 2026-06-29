"""Tests for alert-rule CRUD under a wishlist item (Phase 4).

Uses the shared `client` + `auth_headers` fixtures from conftest.py.
"""


def _create_item(client, auth_headers, **overrides):
    payload = {"title": "Speaker", "query": "speaker"}
    payload.update(overrides)
    resp = client.post("/wishlist", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_create_and_list_alert(client, auth_headers):
    item_id = _create_item(client, auth_headers, target_price="100.00")

    resp = client.post(
        f"/wishlist/{item_id}/alerts",
        json={"rule": "below_target", "threshold": "90.00"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["rule"] == "below_target"
    assert body["channel"] == "email"
    assert body["last_fired_at"] is None

    listed = client.get(f"/wishlist/{item_id}/alerts", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_below_target_without_target_or_threshold_rejected(client, auth_headers):
    item_id = _create_item(client, auth_headers)  # no target_price
    resp = client.post(
        f"/wishlist/{item_id}/alerts",
        json={"rule": "below_target"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_pct_drop_requires_threshold(client, auth_headers):
    item_id = _create_item(client, auth_headers)
    resp = client.post(
        f"/wishlist/{item_id}/alerts",
        json={"rule": "pct_drop"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_delete_alert(client, auth_headers):
    item_id = _create_item(client, auth_headers, target_price="100.00")
    created = client.post(
        f"/wishlist/{item_id}/alerts",
        json={"rule": "below_target"},
        headers=auth_headers,
    ).json()

    resp = client.delete(
        f"/wishlist/{item_id}/alerts/{created['id']}", headers=auth_headers
    )
    assert resp.status_code == 204

    listed = client.get(f"/wishlist/{item_id}/alerts", headers=auth_headers)
    assert listed.json() == []


def test_alerts_are_owner_scoped(client, auth_headers):
    """Another user can't see or add alerts on someone else's item."""
    item_id = _create_item(client, auth_headers, target_price="100.00")

    # second user
    other = {"email": "bob@example.com", "password": "supersecret"}
    client.post("/auth/register", json=other)
    token = client.post(
        "/auth/login",
        data={"username": other["email"], "password": other["password"]},
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}

    resp = client.get(f"/wishlist/{item_id}/alerts", headers=other_headers)
    assert resp.status_code == 404
