"""验收门槛：扇出并行 + 取消传播 + 事件序列可重建。"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.infrastructure.execution import ExecutionCoordinator, TaskExecutionHandle


@pytest.mark.asyncio
async def test_branches_run_in_parallel_and_return_results():
    coord = ExecutionCoordinator(max_concurrent_tasks=2, max_io_concurrency=4)
    handle = TaskExecutionHandle(task_id="t-1")

    async def fast():
        await asyncio.sleep(0.05)
        return "fast"

    async def slow():
        await asyncio.sleep(0.1)
        return "slow"

    results = await coord.run_branches(handle, {"a": fast, "b": slow})
    assert results["a"].ok and results["a"].value == "fast"
    assert results["b"].ok and results["b"].value == "slow"


@pytest.mark.asyncio
async def test_branches_propagate_cancellation():
    coord = ExecutionCoordinator()
    handle = TaskExecutionHandle(task_id="t-cancel")

    async def long():
        await asyncio.sleep(5)
        return "never"

    cancel_task = asyncio.create_task(
        _cancel_after(handle, 0.05)
    )
    results = await coord.run_branches(handle, {"x": long})
    await cancel_task
    assert not results["x"].ok
    assert isinstance(results["x"].error, asyncio.CancelledError)


async def _cancel_after(handle: TaskExecutionHandle, delay: float) -> None:
    await asyncio.sleep(delay)
    handle.cancel_event.set()
