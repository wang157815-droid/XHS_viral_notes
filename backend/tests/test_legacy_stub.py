from __future__ import annotations

from fastapi.testclient import TestClient

from viral_app import app


def test_legacy_root_returns_maintenance_page():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "RedMuse 已迁移到新版工作台" in response.text
    assert "/login" in response.text


def test_legacy_health_marks_stub():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "legacy_stub"
    assert "backend/run.py" in payload["new_backend"]


def test_legacy_api_returns_gone_with_replacement_hint():
    client = TestClient(app)
    response = client.post("/api/viral/search", json={"keywords": ["巧克力"]})
    assert response.status_code == 410
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "LEGACY_API_GONE"
    assert "/api/v1/tasks" in payload["error"]["details"]["replacement"]
