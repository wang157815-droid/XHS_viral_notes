"""RedisStreamBacklog 单测：用 fake redis 验证 append / fetch_after 行为。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from backend.app.domain.events import TaskEvent, TaskEventType


class FakeStreamRedis:
    """最小 fake：支持 xadd / xrange / delete / expire。"""

    def __init__(self):
        self.streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self.expire_calls: List[Tuple[str, int]] = []
        self._counter = 0

    async def xadd(self, key, fields, maxlen=None, approximate=True):
        self._counter += 1
        stream_id = f"{self._counter}-0"
        stream = self.streams.setdefault(key, [])
        stream.append((stream_id, dict(fields)))
        if maxlen and len(stream) > maxlen:
            # approximate 时按整体长度修剪
            del stream[0 : len(stream) - maxlen]
        return stream_id

    async def xrange(self, key, min="-", max="+", count=None):  # noqa: A002
        return list(self.streams.get(key, []))

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    async def delete(self, key):
        self.streams.pop(key, None)
        return 1

    async def ping(self):
        return True

    async def aclose(self):
        pass


@pytest.fixture
def fake_stream_redis(monkeypatch):
    fake = FakeStreamRedis()

    async def _get_fake(settings=None):
        return fake

    from backend.app.infrastructure.cache import redis_client

    monkeypatch.setattr(redis_client, "get_redis", _get_fake)
    return fake


def _make_event(task_id: str, seq: int, event_id: str, payload_text: str) -> TaskEvent:
    return TaskEvent(
        event_id=event_id,
        sequence_id=seq,
        type=TaskEventType.LOG,
        task_id=task_id,
        timestamp=f"2026-04-20T00:00:{seq:02d}+00:00",
        payload={"message": payload_text},
    )


@pytest.mark.asyncio
async def test_append_writes_to_stream(fake_stream_redis):
    from backend.app.infrastructure.event_bus.backlog_store import RedisStreamBacklog

    backlog = RedisStreamBacklog()
    ev = _make_event("task_1", 1, "evt_1", "hello")
    await backlog._append_async(ev)

    assert len(fake_stream_redis.streams["backlog:task:task_1"]) == 1
    assert fake_stream_redis.expire_calls, "应设过期时间"


@pytest.mark.asyncio
async def test_fetch_after_async_no_filter_returns_all(fake_stream_redis):
    from backend.app.infrastructure.event_bus.backlog_store import RedisStreamBacklog

    backlog = RedisStreamBacklog()
    for i in range(3):
        await backlog._append_async(_make_event("task_x", i + 1, f"evt_{i}", f"msg {i}"))

    events = await backlog.fetch_after_async("task_x")
    assert len(events) == 3
    assert events[0].sequence_id == 1


@pytest.mark.asyncio
async def test_fetch_after_event_id_skips_seen(fake_stream_redis):
    from backend.app.infrastructure.event_bus.backlog_store import RedisStreamBacklog

    backlog = RedisStreamBacklog()
    for i in range(4):
        await backlog._append_async(_make_event("task_y", i + 1, f"evt_{i}", "x"))

    events = await backlog.fetch_after_async("task_y", last_event_id="evt_1")
    # 跳过 evt_0 evt_1,返回 evt_2 evt_3
    assert [e.event_id for e in events] == ["evt_2", "evt_3"]


@pytest.mark.asyncio
async def test_fetch_after_sequence_id_fallback(fake_stream_redis):
    from backend.app.infrastructure.event_bus.backlog_store import RedisStreamBacklog

    backlog = RedisStreamBacklog()
    for i in range(3):
        await backlog._append_async(_make_event("task_z", i + 1, f"evt_{i}", "x"))

    events = await backlog.fetch_after_async(
        "task_z", last_event_id="not-exists", last_sequence_id=1
    )
    assert [e.sequence_id for e in events] == [2, 3]


@pytest.mark.asyncio
async def test_drop_removes_stream(fake_stream_redis):
    from backend.app.infrastructure.event_bus.backlog_store import RedisStreamBacklog

    backlog = RedisStreamBacklog()
    await backlog._append_async(_make_event("t1", 1, "e1", "x"))
    assert "backlog:task:t1" in fake_stream_redis.streams
    await backlog._drop_async("t1")
    assert "backlog:task:t1" not in fake_stream_redis.streams


def test_backlog_factory_respects_env(monkeypatch):
    """BACKLOG_BACKEND env 控制工厂返回哪个后端。"""
    from backend.app.infrastructure.event_bus import backlog_store

    backlog_store.reset_backlog_store_cache()
    monkeypatch.setenv("BACKLOG_BACKEND", "memory")
    instance_memory = backlog_store.get_default_backlog_store()
    assert isinstance(instance_memory, backlog_store.InMemoryRingBuffer)

    backlog_store.reset_backlog_store_cache()
    monkeypatch.setenv("BACKLOG_BACKEND", "redis")
    instance_redis = backlog_store.get_default_backlog_store()
    assert isinstance(instance_redis, backlog_store.RedisStreamBacklog)

    backlog_store.reset_backlog_store_cache()
