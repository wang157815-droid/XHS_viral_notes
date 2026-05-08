"""
任务状态机（7 态）。

- pending: 已接收，等待校验入队
- queued: 已入队，等待执行额度
- running: 执行中
- paused: 用户暂停
- completed: 执行成功
- failed: 执行失败
- cancelled: 用户取消
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskStatusTransition:
    source: TaskStatus
    target: TaskStatus
    reason: str


_TERMINAL: FrozenSet[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


_ALLOWED: Dict[TaskStatus, FrozenSet[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class TaskStatusMachine:
    def allowed_next(self, status: TaskStatus) -> FrozenSet[TaskStatus]:
        return _ALLOWED.get(status, frozenset())

    def can_transition(self, source: TaskStatus, target: TaskStatus) -> bool:
        return target in self.allowed_next(source)

    def assert_transition(self, source: TaskStatus, target: TaskStatus) -> None:
        if not self.can_transition(source, target):
            raise ValueError(f"非法任务状态转换: {source.value} -> {target.value}")

    def is_terminal(self, status: TaskStatus) -> bool:
        return status in _TERMINAL

    def list_transitions(self) -> Iterable[TaskStatusTransition]:
        for source, targets in _ALLOWED.items():
            for target in targets:
                yield TaskStatusTransition(source=source, target=target, reason="")


task_status_machine = TaskStatusMachine()
