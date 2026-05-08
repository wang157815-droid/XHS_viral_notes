"""验收门槛：幂等键 + 乐观锁。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.application.optimistic_locking import ModuleVersionGuard
from backend.app.infrastructure.idempotency_store import IdempotencyStore, RedisIdempotencyStore


def test_idempotency_store_first_call_returns_true():
    store = IdempotencyStore()
    assert store.begin("key-a") is True
    assert store.begin("key-a") is False  # 并发冲突


def test_idempotency_store_completes_and_replays():
    store = IdempotencyStore()
    store.begin("key-b")
    store.complete("key-b", status_code=200, body={"ok": True, "data": {"replay": 1}})
    record = store.lookup("key-b")
    assert record and record.completed and record.body and record.body["ok"] is True


@pytest.mark.asyncio
async def test_redis_idempotency_store_conflict_replay_and_abort(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.data = {}

        async def eval(self, script, numkeys, key, payload, ttl):
            if key in self.data:
                return 0
            self.data[key] = payload
            return 1

        async def set(self, key, payload, ex=None):
            self.data[key] = payload

        async def get(self, key):
            return self.data.get(key)

        async def delete(self, key):
            self.data.pop(key, None)

    fake = FakeRedis()

    async def _fake_get_redis():
        return fake

    import backend.app.infrastructure.idempotency_store as mod

    monkeypatch.setattr(mod, "get_redis", _fake_get_redis)

    store = RedisIdempotencyStore()
    assert await store.begin("k") is True
    assert await store.begin("k") is False
    await store.complete("k", 200, {"ok": True})
    record = await store.lookup("k")
    assert record and record.completed and record.body == {"ok": True}
    await store.abort("k")
    assert await store.lookup("k") is None


def test_version_guard_rejects_missing_if_match():
    guard = ModuleVersionGuard(None)
    with pytest.raises(HTTPException) as exc:
        guard.ensure_matches(current_version=3, module_id="mod-x")
    assert exc.value.status_code == 400


def test_version_guard_rejects_mismatch():
    guard = ModuleVersionGuard("2")
    with pytest.raises(HTTPException) as exc:
        guard.ensure_matches(current_version=3, module_id="mod-x")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "INPUT_MODULE_VERSION_MISMATCH"


def test_version_guard_accepts_match():
    guard = ModuleVersionGuard("3")
    guard.ensure_matches(current_version=3, module_id="mod-x")
