"""Phase 0 smoke test: the app boots and /health responds.

Runs without Postgres/Redis because /health is pure liveness. Run with: pytest
"""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["app"] == "deal-hunter"
