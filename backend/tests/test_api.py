import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_status():
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "games_cached" in data
    assert "tracking_enabled" in data
