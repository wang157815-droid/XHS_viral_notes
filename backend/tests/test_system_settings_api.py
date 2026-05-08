"""/api/v1/settings/system API 集成测试（阶段 4.α 补丁）。

验证 GET/PUT 真实持久化,修复"点保存 → 后端 echo 但不存储"的漏洞。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_tmp_store(tmp_path, monkeypatch):
    """替换 SystemSettingsStore 单例,使用 tmp_path 隔离文件。"""
    from backend.app.services import system_settings_store as mod

    store = mod.SystemSettingsStore(store_file=str(tmp_path / "system_settings.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_system_settings_store", lambda: store)

    # settings 路由内部用 `from ... import get_system_settings_store`,
    # 导入后已经是本地名,需要同步 patch
    from backend.app.api.routes import settings as settings_route

    monkeypatch.setattr(settings_route, "get_system_settings_store", lambda: store)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_admin():
        return {"user_id": "user_admin", "nickname": "admin", "role": "admin", "username": "admin"}

    app.dependency_overrides[get_current_user] = _fake_admin

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_get_returns_defaults_initially(client_with_tmp_store: TestClient):
    r = client_with_tmp_store.get("/api/v1/settings/system")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["text_model"] == "deepseek-chat"
    assert data["crawler_schedule"]["enabled"] is True
    assert data["crawler_schedule"]["interval_hours"] == 6


def test_put_persists_and_roundtrips(client_with_tmp_store: TestClient):
    payload = {
        "text_model": "qwen-max",
        "vision_model": "qwen3-vl-plus",
        "embedding_model": "text-embedding-v4",
        "video_analysis_enabled": False,
        "crawler_schedule": {
            "enabled": False,
            "interval_hours": 12,
            "hot_keywords_top_n": 20,
        },
    }
    r1 = client_with_tmp_store.put("/api/v1/settings/system", json=payload)
    assert r1.status_code == 200

    r2 = client_with_tmp_store.get("/api/v1/settings/system")
    assert r2.status_code == 200
    data = r2.json()["data"]
    assert data["text_model"] == "qwen-max"
    assert data["video_analysis_enabled"] is False
    assert data["crawler_schedule"]["enabled"] is False
    assert data["crawler_schedule"]["interval_hours"] == 12
    assert data["crawler_schedule"]["hot_keywords_top_n"] == 20


def test_put_requires_admin(monkeypatch, tmp_path):
    """非管理员 PUT 应该返回 403。"""
    from backend.app.services import system_settings_store as mod

    store = mod.SystemSettingsStore(store_file=str(tmp_path / "system_settings.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_system_settings_store", lambda: store)

    from backend.app.api.routes import settings as settings_route

    monkeypatch.setattr(settings_route, "get_system_settings_store", lambda: store)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_user():
        return {"user_id": "user_u", "nickname": "u", "role": "user", "username": "u"}

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        client = TestClient(app)
        payload = {
            "text_model": "x",
            "vision_model": "y",
            "embedding_model": "z",
            "video_analysis_enabled": True,
            "crawler_schedule": {"enabled": True, "interval_hours": 6, "hot_keywords_top_n": 50},
        }
        r = client.put("/api/v1/settings/system", json=payload)
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
