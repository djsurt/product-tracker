"""Phase 1 tests: auth flow, wishlist CRUD, and ownership isolation."""


def test_register_and_login(client):
    creds = {"email": "bob@example.com", "password": "password123"}

    r = client.post("/auth/register", json=creds)
    assert r.status_code == 201
    assert r.json()["email"] == creds["email"]
    assert "hashed_password" not in r.json()  # never leak the hash

    # duplicate email rejected
    assert client.post("/auth/register", json=creds).status_code == 409

    r = client.post(
        "/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"

    # wrong password rejected
    bad = client.post(
        "/auth/login", data={"username": creds["email"], "password": "nope"}
    )
    assert bad.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/wishlist").status_code == 401
    assert client.get("/me").status_code == 401


def test_wishlist_crud(client, auth_headers):
    # create
    r = client.post(
        "/wishlist",
        headers=auth_headers,
        json={"title": "Sony WH-1000XM5", "query": "sony wh-1000xm5", "target_price": "299.99"},
    )
    assert r.status_code == 201
    item = r.json()
    item_id = item["id"]
    assert item["is_active"] is True
    assert item["target_price"] == "299.99"

    # list
    r = client.get("/wishlist", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # get one
    assert client.get(f"/wishlist/{item_id}", headers=auth_headers).status_code == 200

    # patch (partial update)
    r = client.patch(
        f"/wishlist/{item_id}", headers=auth_headers, json={"target_price": "250.00"}
    )
    assert r.status_code == 200
    assert r.json()["target_price"] == "250.00"
    assert r.json()["title"] == "Sony WH-1000XM5"  # untouched

    # delete
    assert client.delete(f"/wishlist/{item_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/wishlist/{item_id}", headers=auth_headers).status_code == 404


def test_ownership_isolation(client, auth_headers):
    """A second user must not see or touch the first user's items."""
    # alice (from auth_headers) creates an item
    r = client.post(
        "/wishlist",
        headers=auth_headers,
        json={"title": "iPad", "query": "ipad air"},
    )
    item_id = r.json()["id"]

    # bob registers + logs in
    client.post("/auth/register", json={"email": "bob2@example.com", "password": "password123"})
    token = client.post(
        "/auth/login", data={"username": "bob2@example.com", "password": "password123"}
    ).json()["access_token"]
    bob = {"Authorization": f"Bearer {token}"}

    assert client.get("/wishlist", headers=bob).json() == []  # bob sees nothing
    assert client.get(f"/wishlist/{item_id}", headers=bob).status_code == 404
    assert client.delete(f"/wishlist/{item_id}", headers=bob).status_code == 404
