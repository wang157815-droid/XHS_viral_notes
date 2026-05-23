"""
TaskEventBus：任务事件发布与订阅。

- publish(event)：写入 backlog + 推送到订阅者。
- subscribe(task_id) -> AsyncIterator[TaskEvent]：订阅任务事件流。
- 每个订阅者独立队列，互不影响。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional

from loguru import logger

from ...domain.events import TaskEvent, TaskEventType, task_event_factory
from .backlog_store import TaskEventBacklogStore, get_default_backlog_store


@dataclass
class _Subscription:
    task_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))


class TaskEventBus:
    def __init__(self, backlog: Optional[TaskEventBacklogStore] = None) -> None:
        # 按 env `BACKLOG_BACKEND` 选 memory (默认) 或 redis
        self._backlog: TaskEventBacklogStore = backlog or get_default_backlog_store()
        self._subs: Dict[str, List[_Subscription]] = {}
        self._lock = asyncio.Lock()

    @property
    def backlog(self) -> TaskEventBacklogStore:
        return self._backlog

    async def publish(self, event: TaskEvent) -> None:
        self._backlog.append(event)
        async with self._lock:
            subs = list(self._subs.get(event.task_id, []))
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "[TaskEventBus] 订阅者队列已满，丢弃事件 task_id={} type={}",
                    event.task_id,
                    event.type.value,
                )

    async def publish_event(
        self,
        *,
        task_id: str,
        type: TaskEventType,
        payload: Optional[dict] = None,
        branch_id: Optional[str] = None,
    ) -> TaskEvent:
        event = task_event_factory.create(task_id=task_id, type=type, payload=payload, branch_id=branch_id)
        await self.publish(event)
        return event

    async def subscribe(
        self,
        task_id: str,
        *,
        last_event_id: Optional[str] = None,
        last_sequence_id: Optional[int] = None,
    ) -> AsyncIterator[TaskEvent]:
        """
        订阅任务事件流：
        1. 先回放 backlog 中缺失的事件
        2. 再进入实时模式

        注意：优先使用 backlog 的 async 版本（RedisStreamBacklog.fetch_after_async），
        因为同步版本在 FastAPI async 上下文中检测到 running loop 会直接返回空列表，
        导致服务器上 backlog 回放完全失效，前端连接时丢失早期 Agent 事件。
        """
        sub = _Subscription(task_id=task_id)
        async with self._lock:
            self._subs.setdefault(task_id, []).append(sub)

        try:
            # 优先调用 async 版本（Redis 后端），避免在 running loop 中返回空
            if hasattr(self._backlog, "fetch_after_async"):
                missed = await self._backlog.fetch_after_async(  # type: ignore[attr-defined]
                    task_id,
                    last_event_id=last_event_id,
                    last_sequence_id=last_sequence_id,
                )
            else:
                missed = self._backlog.fetch_after(
                    task_id,
                    last_event_id=last_event_id,
                    last_sequence_id=last_sequence_id,
                )

            # 记录已从 backlog 回放的 event_id，防止 await 期间新入队的事件被重复推送
            seen: set = {ev.event_id for ev in missed}
            for event in missed:
                yield event

            # 如果 DONE 已在 backlog 中回放，直接终止——不能再进入 while True，
            # 否则 queue.get() 永远阻塞（已完成的任务不再产生新事件），
            # 导致 SSE 连接挂起并造成 _subs 字典泄漏。
            if any(ev.type == TaskEventType.DONE for ev in missed):
                return

            while True:
                event = await sub.queue.get()
                if event.event_id in seen:
                    # 该事件已在 backlog 回放中发送过，不重复 yield。
                    # 但若是 DONE，仍需终止生成器（处理 fetch_after_async await
                    # 期间 DONE 同时入队的竞争窗口）。
                    if event.type == TaskEventType.DONE:
                        return
                    continue
                seen.add(event.event_id)
                yield event
                if event.type == TaskEventType.DONE:
                    return
        finally:
            async with self._lock:
                subs = self._subs.get(task_id)
                if subs and sub in subs:
                    subs.remove(sub)
                if subs is not None and not subs:
                    self._subs.pop(task_id, None)


task_event_bus = TaskEventBus()
