"""
ExecutionCoordinator：并发执行协调器。

职责：
- 限制最大并发任务数（信号量排队）
- 管理单任务内分支并行（asyncio.gather）
- 提供取消传播（cancel(task_id) 会取消未完成分支）
- 提供轻量 I/O 信号量（外部 API/爬虫并发保护）

注意：
- 本类归属 infrastructure 层，不承载业务逻辑。
- 应用层（TaskService/Orchestrator）通过 run_task / run_branches 调用。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger


TaskRunner = Callable[["TaskExecutionHandle"], Awaitable[Any]]
BranchRunner = Callable[[], Awaitable[Any]]


@dataclass
class BranchResult:
    branch_id: str
    ok: bool
    value: Any = None
    error: Optional[BaseException] = None


@dataclass
class TaskExecutionHandle:
    task_id: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    async def wait_until_cancelled(self) -> None:
        await self.cancel_event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise asyncio.CancelledError(f"task {self.task_id} cancelled")


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        max_concurrent_tasks: Optional[int] = None,
        max_io_concurrency: Optional[int] = None,
    ) -> None:
        self._max_tasks = max_concurrent_tasks or int(os.getenv("EXECUTION_MAX_TASKS", "4"))
        self._max_io = max_io_concurrency or int(os.getenv("EXECUTION_MAX_IO", "8"))
        self._task_slots = asyncio.Semaphore(self._max_tasks)
        self._io_slots = asyncio.Semaphore(self._max_io)
        self._active: Dict[str, TaskExecutionHandle] = {}
        self._tasks: Dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def run_task(self, task_id: str, runner: TaskRunner) -> asyncio.Task[Any]:
        """
        启动一个任务执行（异步后台）。

        返回 asyncio.Task，调用方可以 await 或 fire-and-forget。
        """
        handle = TaskExecutionHandle(task_id=task_id)

        async def _wrapped() -> Any:
            async with self._task_slots:
                async with self._lock:
                    self._active[task_id] = handle
                try:
                    return await runner(handle)
                finally:
                    async with self._lock:
                        self._active.pop(task_id, None)
                        self._tasks.pop(task_id, None)

        loop_task = asyncio.create_task(_wrapped(), name=f"task:{task_id}")
        async with self._lock:
            self._tasks[task_id] = loop_task
        return loop_task

    async def run_branches(
        self,
        handle: TaskExecutionHandle,
        branches: Dict[str, BranchRunner],
        *,
        raise_on_error: bool = False,
    ) -> Dict[str, BranchResult]:
        """
        在单任务内并行执行若干分支。

        branches: {"crawler": async_fn, "rag": async_fn, ...}
        返回 {分支名 -> BranchResult}
        """
        if not branches:
            return {}

        async def _branch(name: str, runner: BranchRunner) -> BranchResult:
            if handle.is_cancelled():
                return BranchResult(branch_id=name, ok=False, error=asyncio.CancelledError("cancelled_before_start"))
            try:
                value = await runner()
                return BranchResult(branch_id=name, ok=True, value=value)
            except asyncio.CancelledError as exc:
                return BranchResult(branch_id=name, ok=False, error=exc)
            except Exception as exc:
                logger.exception("[ExecutionCoordinator] branch {} failed: {}", name, exc)
                return BranchResult(branch_id=name, ok=False, error=exc)

        branch_tasks: Dict[str, asyncio.Task[BranchResult]] = {
            name: asyncio.create_task(_branch(name, runner), name=f"branch:{name}")
            for name, runner in branches.items()
        }

        async def _cancel_watcher() -> None:
            await handle.cancel_event.wait()
            for t in branch_tasks.values():
                if not t.done():
                    t.cancel()

        watcher = asyncio.create_task(_cancel_watcher(), name="branch-cancel-watcher")
        try:
            await asyncio.gather(*branch_tasks.values(), return_exceptions=True)
        finally:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

        results: Dict[str, BranchResult] = {}
        for name, t in branch_tasks.items():
            if t.cancelled():
                results[name] = BranchResult(branch_id=name, ok=False, error=asyncio.CancelledError())
                continue
            exc = t.exception()
            if exc is not None:
                results[name] = BranchResult(branch_id=name, ok=False, error=exc)
            else:
                results[name] = t.result()

        if raise_on_error:
            for result in results.values():
                if not result.ok and result.error:
                    raise result.error

        return results

    async def acquire_io(self) -> "_IoSlot":
        return _IoSlot(self._io_slots)

    async def cancel(self, task_id: str) -> bool:
        async with self._lock:
            handle = self._active.get(task_id)
            task = self._tasks.get(task_id)
        if handle:
            handle.cancel_event.set()
        if task and not task.done():
            task.cancel()
        return bool(handle or task)

    def is_running(self, task_id: str) -> bool:
        return task_id in self._active

    def stats(self) -> Dict[str, Any]:
        return {
            "max_tasks": self._max_tasks,
            "max_io": self._max_io,
            "active_tasks": len(self._active),
        }


class _IoSlot:
    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore

    async def __aenter__(self) -> "_IoSlot":
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


execution_coordinator = ExecutionCoordinator()
