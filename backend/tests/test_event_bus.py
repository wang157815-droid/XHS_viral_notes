"""验收门槛：SSE 事件总线 + 回放 + 订阅。"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from backend.app.domain.events import TaskEvent, TaskEventType
from backend.app.infrastructure.event_bus.backlog_store import InMemoryRingBuffer
from backend.app.infrastructure.event_bus.task_event_bus import TaskEventBus


@pytest.mark.asyncio
async def test_backlog_store_fetch_after_last_event_id():
    store = InMemoryRingBuffer()
    task_id = "task-x"
    events: List[TaskEvent] = [
        TaskEvent(event_id="e-1", sequence_id=1, type=TaskEventType.LOG, task_id=task_id, timestamp=""),
        TaskEvent(event_id="e-2", sequence_id=2, type=TaskEventType.LOG, task_id=task_id, timestamp=""),
        TaskEvent(event_id="e-3", sequence_id=3, type=TaskEventType.LOG, task_id=task_id, timestamp=""),
    ]
    for e in events:
        store.append(e)

    missed = store.fetch_after(task_id, last_event_id="e-1")
    assert [e.event_id for e in missed] == ["e-2", "e-3"]

    missed_by_seq = store.fetch_after(task_id, last_sequence_id=1)
    assert [e.event_id for e in missed_by_seq] == ["e-2", "e-3"]


@pytest.mark.asyncio
async def test_event_bus_subscribe_replays_then_streams_live():
    bus = TaskEventBus()
    task_id = "task-stream"
    # 预先写入 backlog
    await bus.publish_event(task_id=task_id, type=TaskEventType.LOG, payload={"n": 1})
    await bus.publish_event(task_id=task_id, type=TaskEventType.LOG, payload={"n": 2})

    received: List[TaskEvent] = []

    async def consume():
        async for event in bus.subscribe(task_id, last_sequence_id=0):
            received.append(event)
            if event.type == TaskEventType.DONE:
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    await bus.publish_event(task_id=task_id, type=TaskEventType.LOG, payload={"n": 3})
    await bus.publish_event(task_id=task_id, type=TaskEventType.DONE, payload={})

    await asyncio.wait_for(consumer, timeout=2.0)
    assert [e.payload.get("n") for e in received[:3]] == [1, 2, 3]
    assert received[-1].type == TaskEventType.DONE


@pytest.mark.asyncio
async def test_subscribe_terminates_when_done_already_in_backlog():
    """回归测试：任务完成后客户端重连时，DONE 已在 backlog 中，生成器必须终止。

    Bug(bdc1e4a)：dedup 的 seen 集合包含了 DONE 的 event_id，导致 DONE 进入
    live queue 时被 continue 跳过，while True 永远阻塞，SSE 连接挂起。
    """
    bus = TaskEventBus()
    task_id = "task-done-before-subscribe"

    # 模拟任务已全部完成（包括 DONE）后，客户端才连接
    await bus.publish_event(task_id=task_id, type=TaskEventType.LOG, payload={"step": 1})
    await bus.publish_event(task_id=task_id, type=TaskEventType.DONE, payload={})

    received: List[TaskEvent] = []

    async def consume():
        # 从头订阅（last_sequence_id=0），会从 backlog 拿到全部事件含 DONE
        async for event in bus.subscribe(task_id, last_sequence_id=0):
            received.append(event)

    # 如果生成器挂起，asyncio.wait_for 会超时并抛出 TimeoutError
    await asyncio.wait_for(consume(), timeout=2.0)

    assert len(received) == 2
    assert received[-1].type == TaskEventType.DONE


@pytest.mark.asyncio
async def test_subscribe_terminates_when_done_in_backlog_and_queue():
    """回归测试：DONE 同时出现在 backlog 和 live queue（竞争窗口）时，生成器必须终止。

    场景：fetch_after_async 的 await 期间 DONE 被发布 → 它进了 backlog 也进了 queue。
    dedup 逻辑正确跳过 queue 里的重复 DONE，但必须通过 return 而非 continue 终止。
    """
    bus = TaskEventBus()
    task_id = "task-done-race"

    # 发布部分事件后，subscriber 注册；然后再发 DONE，使其同时落在 backlog 和 queue
    await bus.publish_event(task_id=task_id, type=TaskEventType.LOG, payload={"step": 1})

    received: List[TaskEvent] = []

    async def consume():
        async for event in bus.subscribe(task_id, last_sequence_id=0):
            received.append(event)

    consumer = asyncio.create_task(consume())
    # 让 consume 刚完成 subscription 注册 + backlog fetch，再发 DONE
    await asyncio.sleep(0.02)
    await bus.publish_event(task_id=task_id, type=TaskEventType.DONE, payload={})

    await asyncio.wait_for(consumer, timeout=2.0)
    event_types = [e.type for e in received]
    assert TaskEventType.DONE in event_types
    # DONE 只应出现一次（无重复投递）
    assert event_types.count(TaskEventType.DONE) == 1
