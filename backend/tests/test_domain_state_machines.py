"""验收门槛：任务/模块状态机行为。"""

from __future__ import annotations

import pytest

from backend.app.domain.module_status import ModuleStatus, module_status_machine
from backend.app.domain.task_status import TaskStatus, task_status_machine


def test_task_status_machine_allows_core_flow():
    chain = [
        (TaskStatus.PENDING, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.COMPLETED),
    ]
    for src, dst in chain:
        assert task_status_machine.can_transition(src, dst)
    assert task_status_machine.is_terminal(TaskStatus.COMPLETED)


def test_task_status_machine_rejects_illegal_transition():
    with pytest.raises(ValueError):
        task_status_machine.assert_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)


def test_module_status_machine_supports_dirty_cycle():
    cycle = [
        (ModuleStatus.PENDING, ModuleStatus.GENERATING),
        (ModuleStatus.GENERATING, ModuleStatus.READY),
        (ModuleStatus.READY, ModuleStatus.STALE),
        (ModuleStatus.STALE, ModuleStatus.GENERATING),
        (ModuleStatus.GENERATING, ModuleStatus.READY),
    ]
    for src, dst in cycle:
        assert module_status_machine.can_transition(src, dst)


def test_module_status_machine_rejects_ready_to_pending():
    with pytest.raises(ValueError):
        module_status_machine.assert_transition(ModuleStatus.READY, ModuleStatus.PENDING)
