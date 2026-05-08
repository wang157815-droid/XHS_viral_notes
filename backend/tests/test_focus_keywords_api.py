"""/api/v1/settings/focus-keywords + warmup 读取链路 集成测试(阶段 4.α 补丁)。

验证"前端 PUT → 后端持久化 → warmup 读到"完整闭环。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """替换 FocusKeywordsStore 单例为 tmp 文件,并伪造管理员登录。"""
    from backend.app.services import focus_keywords_store as mod

    store = mod.FocusKeywordsStore(store_file=str(tmp_path / "focus_keywords.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_focus_keywords_store", lambda: store)

    from backend.app.api.routes import settings as settings_route

    monkeypatch.setattr(settings_route, "get_focus_keywords_store", lambda: store)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_admin():
        return {"user_id": "user_admin", "nickname": "admin", "role": "admin", "username": "admin"}

    app.dependency_overrides[get_current_user] = _fake_admin

    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_get_empty_initially(admin_client):
    client, _ = admin_client
    r = client.get("/api/v1/settings/focus-keywords")
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []


def test_put_persists_and_normalizes(admin_client):
    client, store = admin_client
    r = client.put(
        "/api/v1/settings/focus-keywords",
        json={"items": ["  巧克力  ", "巧克力", "", "Fazer", "fazer"]},
    )
    assert r.status_code == 200
    assert r.json()["data"]["items"] == ["巧克力", "Fazer"]

    r2 = client.get("/api/v1/settings/focus-keywords")
    assert r2.json()["data"]["items"] == ["巧克力", "Fazer"]


def test_put_requires_admin(monkeypatch, tmp_path):
    from backend.app.services import focus_keywords_store as mod

    store = mod.FocusKeywordsStore(store_file=str(tmp_path / "focus.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_focus_keywords_store", lambda: store)

    from backend.app.api.routes import settings as settings_route

    monkeypatch.setattr(settings_route, "get_focus_keywords_store", lambda: store)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_user():
        return {"user_id": "u", "nickname": "u", "role": "user", "username": "u"}

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        client = TestClient(app)
        r = client.put("/api/v1/settings/focus-keywords", json={"items": ["x"]})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_warmup_reads_focus_keywords_from_store(tmp_path, monkeypatch):
    """核心闭环：store 写入 → warmup._collect_warmup_keywords 读到。"""
    from backend.app.services import focus_keywords_store as mod

    store = mod.FocusKeywordsStore(store_file=str(tmp_path / "focus.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_focus_keywords_store", lambda: store)

    await store.replace(["巧克力", "北欧", "Fazer"])

    from backend.app.infrastructure.queue.tasks import warmup

    async def _empty_top_queries():
        return []

    monkeypatch.setattr(warmup, "_load_top_queries_from_redis", _empty_top_queries)

    merged = await warmup._collect_warmup_keywords()
    assert merged == ["巧克力", "北欧", "Fazer"]


@pytest.mark.asyncio
async def test_warmup_merges_store_and_hot_queries(tmp_path, monkeypatch):
    """store 和 Redis 热词都读,store 优先 + 去重。"""
    from backend.app.services import focus_keywords_store as mod

    store = mod.FocusKeywordsStore(store_file=str(tmp_path / "focus.json"))
    monkeypatch.setattr(mod, "_default_store", store, raising=False)
    monkeypatch.setattr(mod, "get_focus_keywords_store", lambda: store)

    await store.replace(["巧克力", "北欧"])

    from backend.app.infrastructure.queue.tasks import warmup

    async def _fake_top():
        return ["护肤", "巧克力", "咖啡"]

    monkeypatch.setattr(warmup, "_load_top_queries_from_redis", _fake_top)

    merged = await warmup._collect_warmup_keywords()
    # focus 优先,且"巧克力"只出现一次
    assert merged == ["巧克力", "北欧", "护肤", "咖啡"]
