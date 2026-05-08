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
