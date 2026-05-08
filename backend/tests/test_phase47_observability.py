from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_summary_requires_admin_and_returns_trace_id():
    from backend.app.main import app

    client = TestClient(app)
    response = client.get("/api/v1/metrics/summary", headers={"X-Request-Id": "trace-test-1"})

    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["trace_id"] == "trace-test-1"
    assert response.headers["X-Request-Id"] == "trace-test-1"


def test_metrics_summary_admin(monkeypatch):
    from backend.app.api.routes import metrics as metrics_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app

    class FakeMetricsStore:
        def get_summary(self, *, window_hours: int = 24):
            return {
                "window_hours": window_hours,
                "tasks": {
                    "total": 3,
                    "completed": 2,
                    "failed": 1,
                    "cancelled": 0,
                    "failure_rate": 0.3333,
                    "p50_ms": 100,
                    "p95_ms": 250,
                },
                "models": {
                    "calls": 5,
                    "failed_calls": 1,
                    "tokens_in": 120,
                    "tokens_out": 80,
                    "avg_duration_ms": 300,
                },
                "top_errors": [{"error_code": "MODEL_TIMEOUT", "count": 1}],
                "recent_eval_runs": [],
            }

    def fake_admin():
        return {"user_id": "admin", "nickname": "admin", "role": "admin", "username": "admin"}

    monkeypatch.setattr(metrics_route, "metrics_store", FakeMetricsStore())
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        client = TestClient(app)
        response = client.get("/api/v1/metrics/summary?window_hours=7")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["window_hours"] == 7
        assert data["tasks"]["p95_ms"] == 250
        assert data["models"]["calls"] == 5
    finally:
        app.dependency_overrides.pop(get_current_user, None)
