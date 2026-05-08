"""L1 关键词缓存单测：用 fakeredis mock Redis,验证 key 规范化/命中/失效/TTL。"""

from __future__ import annotations

from typing import Any, List

import pytest


class FakeAsyncRedis:
    """最小 fakeredis：支持 get/setex/delete/exists/ttl。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.ops: List[tuple] = []

    async def get(self, key):
        self.ops.append(("get", key))
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.ops.append(("setex", key, ttl))
        self.store[key] = value
        self.ttls[key] = int(ttl)
        return True

    async def delete(self, key):
        self.ops.append(("delete", key))
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def ttl(self, key):
        return self.ttls.get(key, -2)

    async def aclose(self):
        pass

    async def ping(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    """替换 get_redis 返回 FakeAsyncRedis。"""
    from backend.app.infrastructure.cache import redis_client

    fake = FakeAsyncRedis()

    async def _get_fake(settings=None):
        return fake

    monkeypatch.setattr(redis_client, "get_redis", _get_fake)

    # KeywordCache 通过 `from .redis_client import get_redis` 引用的是 keyword_cache
    # 模块命名空间里的 get_redis,所以也要 patch 那里
    from backend.app.infrastructure.cache import keyword_cache

    monkeypatch.setattr(keyword_cache, "get_redis", _get_fake)
    return fake


@pytest.mark.asyncio
async def test_key_normalization_is_order_insensitive(fake_redis):
    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache()
    assert c._key(["b", "a"]) == c._key(["a", "b"])
    assert c._key(["a", "a", " b "]) == c._key(["a", "b"])


@pytest.mark.asyncio
async def test_set_and_get_roundtrip(fake_redis):
    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache(ttl_sec=3600)
    notes = [{"note_id": "n1", "title": "hi"}, {"note_id": "n2", "title": "bye"}]
    ok = await c.set(["巧克力"], notes)
    assert ok

    cached = await c.get(["巧克力"])
    assert cached is not None
    assert len(cached) == 2
    assert cached[0]["note_id"] == "n1"

    desc = await c.describe(["巧克力"])
    assert desc["exists"] is True
    assert desc["ttl_sec"] <= 3600


@pytest.mark.asyncio
async def test_miss_returns_none(fake_redis):
    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache()
    assert await c.get(["不存在的关键词"]) is None


@pytest.mark.asyncio
async def test_invalidate_removes_entry(fake_redis):
    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache()
    await c.set(["a"], [{"note_id": "x"}])
    assert await c.get(["a"]) is not None

    ok = await c.invalidate(["a"])
    assert ok is True
    assert await c.get(["a"]) is None


@pytest.mark.asyncio
async def test_redis_failure_degrades_gracefully(monkeypatch):
    """Redis 不可用时 cache.get/set 返回 None/False,不抛异常。"""
    from backend.app.infrastructure.cache import keyword_cache
    from backend.app.infrastructure.cache.redis_client import RedisUnavailable

    async def _raise_unavailable(settings=None):
        raise RedisUnavailable("mocked down")

    monkeypatch.setattr(keyword_cache, "get_redis", _raise_unavailable)

    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache()
    assert await c.get(["k"]) is None
    assert await c.set(["k"], [{"id": "x"}]) is False


@pytest.mark.asyncio
async def test_empty_inputs_return_early(fake_redis):
    from backend.app.infrastructure.cache.keyword_cache import KeywordCache

    c = KeywordCache()
    assert await c.get([]) is None
    assert await c.set([], [{"id": "x"}]) is False
    assert await c.set(["a"], []) is False  # 空 notes 不写
