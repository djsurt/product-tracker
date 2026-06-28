"""Test fixtures.

We run the suite against an **in-memory SQLite** DB, swapped in via FastAPI's
`dependency_overrides`. This keeps tests fast and hermetic (no Postgres needed in
CI) — and works precisely because the models use SQLAlchemy's portable generic
types (`Uuid`, `Numeric`). Schema here is created from `Base.metadata`; the
Alembic migration is verified separately against real Postgres.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from core.db import Base, get_db

# import models so they register on Base.metadata before create_all
import core.models  # noqa: F401


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory DB across connections
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    """Register + log in a user, returning Authorization headers."""
    creds = {"email": "alice@example.com", "password": "supersecret"}
    client.post("/auth/register", json=creds)
    resp = client.post(
        "/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
