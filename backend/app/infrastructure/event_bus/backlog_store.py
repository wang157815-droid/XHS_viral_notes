"""
TaskEventBacklogStore：事件回放存储抽象。

阶段2默认实现：`InMemoryRingBuffer`
- 按 task_id 维护有限长度环形缓冲区（默认 500 事件）
- 支持 by_event_id / by_sequence_id 截取尾部
- TTL：事件保留时间（默认 300s）超出即丢弃

阶段 4.α 新增 `RedisStreamBacklog`（Redis Streams 后端）
- 跨进程共享(多 worker 也能订阅 backlog)
- MAXLEN~ 自动修剪,避免无限增长
- 通过 env `BACKLOG_BACKEND=memory|redis` 切换

存储工厂：`get_default_backlog_store()` 会按 env 返回合适实例
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from threading import RLock
from typing import Deque, Dict, List, Optional, Protocol

from loguru import logger

from ...domain.events import TaskEvent, TaskEventType


class TaskEventBacklogStore(Protocol):
    def append(self, event: TaskEvent) -> None: ...

    def fetch_after(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> List[TaskEvent]: ...

    def drop(self, task_id: str) -> None: ...


class InMemoryRingBuffer:
    def __init__(self, *, per_task_capacity: int = 1000, ttl_seconds: int = 3600) -> None:
        self._capacity = per_task_capacity
        self._ttl = ttl_seconds
        self._buffers: Dict[str, Deque[tuple[float, TaskEvent]]] = {}
        self._lock = RLock()

    def _buf(self, task_id: str) -> Deque[tuple[float, TaskEvent]]:
        return self._buffers.setdefault(task_id, deque(maxlen=self._capacity))

    def _purge_expired(self, task_id: str) -> None:
        buf = self._buffers.get(task_id)
        if not buf:
            return
        now = time.time()
        while buf and (now - buf[0][0]) > self._ttl:
            buf.popleft()

    def append(self, event: TaskEvent) -> None:
        with self._lock:
            buf = self._buf(event.task_id)
            buf.append((time.time(), event))
            self._purge_expired(event.task_id)

    def fetch_after(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> List[TaskEvent]:
        with self._lock:
            self._purge_expired(task_id)
            buf = self._buffers.get(task_id)
            if not buf:
                return []
            items = [ev for (_, ev) in buf]
            if last_event_id:
                found_idx = -1
                for idx, ev in enumerate(items):
                    if ev.event_id == last_event_id:
                        found_idx = idx
                        break
                if found_idx >= 0:
                    return items[found_idx + 1 :]

            if last_sequence_id is not None:
                return [ev for ev in items if ev.sequence_id > last_sequence_id]

            return list(items)

    def drop(self, task_id: str) -> None:
        with self._lock:
            self._buffers.pop(task_id, None)


class RedisStreamBacklog:
    """Redis Streams 后端的事件 backlog（阶段 4.α）。

    key 格式：`backlog:task:{task_id}` → Redis Stream
    字段：
      - event_id:       client-side event_id (UUID)
      - sequence_id:    agent 内按 task 单调自增
      - payload:        JSON 序列化的完整 TaskEvent.to_dict()
    使用 MAXLEN~ 作近似修剪,避免 stream 无限增长。

    **注意**：Redis 操作是异步的（redis.asyncio）,但本接口要符合现有同步 Protocol。
    内部用 `asyncio.get_running_loop().run_until_complete` 桥接。
    如果在 async 上下文被调用,事件循环嵌套会报错 —— 我们通过
    `asyncio.ensure_future + loop.create_task` 做 fire-and-forget。
    """

    STREAM_KEY_FMT = "backlog:task:{task_id}"

    def __init__(
        self,
        *,
        per_task_capacity: int = 1000,
        ttl_seconds: int = 3600,
    ) -> None:
        self._capacity = per_task_capacity
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------
    # Protocol methods（同步接口保持向后兼容）
    # ------------------------------------------------------------------

    def append(self, event: TaskEvent) -> None:
        """同步接口：fire-and-forget 到事件循环。"""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._append_async(event))
            else:
                loop.run_until_complete(self._append_async(event))
        except RuntimeError:
            # 没有事件循环就降级什么都不做,避免阻塞调用方
            logger.warning("[RedisStreamBacklog.append] 无事件循环,本次事件未入 backlog")

    def fetch_after(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> List[TaskEvent]:
        """同步接口：跑事件循环拿结果。

        注意：若从 async handler 调用,应该用 `fetch_after_async` 替代。
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 不能在 running loop 里 run_until_complete,让调用方用 async 接口
                logger.warning(
                    "[RedisStreamBacklog.fetch_after] 检测到 running loop,"
                    "请改用 fetch_after_async(),本次返回空"
                )
                return []
            return loop.run_until_complete(
                self._fetch_after_async(
                    task_id,
                    last_event_id=last_event_id,
                    last_sequence_id=last_sequence_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisStreamBacklog.fetch_after] 失败: {exc}")
            return []

    async def fetch_after_async(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> List[TaskEvent]:
        """async 接口，首选用法（在 FastAPI / arq 里直接 await）。"""
        return await self._fetch_after_async(
            task_id,
            last_event_id=last_event_id,
            last_sequence_id=last_sequence_id,
        )

    def drop(self, task_id: str) -> None:
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._drop_async(task_id))
            else:
                loop.run_until_complete(self._drop_async(task_id))
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # async impl
    # ------------------------------------------------------------------

    async def _append_async(self, event: TaskEvent) -> None:
        try:
            from ..cache.redis_client import get_redis

            client = await get_redis()
            key = self.STREAM_KEY_FMT.format(task_id=event.task_id)
            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            await client.xadd(
                key,
                {
                    "event_id": event.event_id,
                    "sequence_id": str(event.sequence_id),
                    "type": event.type.value,
                    "payload": payload,
                },
                maxlen=self._capacity,
                approximate=True,
            )
            # 给 stream key 设过期时间（最后一次活跃后 ttl 秒销毁）
            await client.expire(key, self._ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisStreamBacklog._append_async] 追加失败: {exc}")

    async def _fetch_after_async(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> List[TaskEvent]:
        try:
            from ..cache.redis_client import get_redis

            client = await get_redis()
            key = self.STREAM_KEY_FMT.format(task_id=task_id)
            # xrange 读全部,Python 侧按 last_event_id/sequence_id 过滤
            # 因为需求是"滚动回放",每次 backlog 数量 ≤ 500,性能可接受
            entries = await client.xrange(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisStreamBacklog._fetch_after_async] 读取失败: {exc}")
            return []

        events: List[TaskEvent] = []
        for _stream_id, fields in entries:
            try:
                raw = fields.get("payload") if isinstance(fields, dict) else None
                if not raw:
                    continue
                data = json.loads(raw)
                events.append(_event_from_dict(data))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[RedisStreamBacklog] 跳过损坏事件: {exc}")

        if last_event_id:
            for idx, ev in enumerate(events):
                if ev.event_id == last_event_id:
                    return events[idx + 1 :]
            # 没找到 last_event_id,按 sequence_id 兜底
        if last_sequence_id is not None:
            return [ev for ev in events if ev.sequence_id > last_sequence_id]
        return events

    async def _drop_async(self, task_id: str) -> None:
        try:
            from ..cache.redis_client import get_redis

            client = await get_redis()
            key = self.STREAM_KEY_FMT.format(task_id=task_id)
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisStreamBacklog._drop_async] 删除失败: {exc}")


def _event_from_dict(data: Dict[str, object]) -> TaskEvent:
    """JSON → TaskEvent 还原（type 枚举手动转）。"""
    return TaskEvent(
        event_id=str(data.get("event_id") or ""),
        sequence_id=int(data.get("sequence_id") or 0),
        type=TaskEventType(str(data.get("type") or "log")),
        task_id=str(data.get("task_id") or ""),
        timestamp=str(data.get("timestamp") or ""),
        payload=dict(data.get("payload") or {}),
        branch_id=data.get("branch_id"),  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# 工厂：按 env `BACKLOG_BACKEND` 返回合适实例
# ----------------------------------------------------------------------

_default_store: Optional[TaskEventBacklogStore] = None


def get_default_backlog_store() -> TaskEventBacklogStore:
    """按 env 返回后端：memory(默认) | redis。"""
    global _default_store
    if _default_store is not None:
        return _default_store

    backend = (os.getenv("BACKLOG_BACKEND") or "memory").strip().lower()
    if backend == "redis":
        logger.info("[backlog_store] 使用 RedisStreamBacklog 后端")
        _default_store = RedisStreamBacklog()
    else:
        logger.info("[backlog_store] 使用 InMemoryRingBuffer 后端")
        _default_store = InMemoryRingBuffer()
    return _default_store


def reset_backlog_store_cache() -> None:
    """测试用：清理工厂缓存。"""
    global _default_store
    _default_store = None


__all__ = [
    "TaskEventBacklogStore",
    "InMemoryRingBuffer",
    "RedisStreamBacklog",
    "get_default_backlog_store",
    "reset_backlog_store_cache",
]
