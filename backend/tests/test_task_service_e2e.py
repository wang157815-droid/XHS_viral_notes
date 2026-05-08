"""验收门槛：E2E 任务创建 + 画布 + 幂等 + 状态机联动。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.application.task_service import TaskService
from backend.app.domain.module_status import ModuleStatus
from backend.app.domain.task_status import TaskStatus
from backend.app.domain.canvas import CanvasModule, build_empty_canvas
from backend.app.infrastructure.repository.task_repository import TaskRepository


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch):
    """使用临时目录隔离 task_repository，防止污染真实数据。"""
    from backend.app.infrastructure import repository as repo_module
    from backend.app.application import task_service as task_service_module

    repo = TaskRepository(storage_dir=tmp_path / "tasks")
    monkeypatch.setattr(repo_module, "task_repository", repo)
    monkeypatch.setattr(task_service_module, "task_repository", repo, raising=False)
    # Also patch the reference inside the task_service module (it imports at top level)
    from backend.app.application import task_service as ts_mod  # noqa: F401

    # Patch module-level reference used inside TaskService
    from backend.app.infrastructure.repository import task_repository as _  # noqa: F401
    import backend.app.application.task_service as ts2

    monkeypatch.setattr(ts2, "task_repository", repo)
    return repo


def test_create_task_writes_owner_and_idem(isolated_repo):
    svc = TaskService()
    result = svc.create_task(
        owner_user_id="user-1",
        raw_input="分析奶油巧克力",
        keywords=["巧克力", "奶油"],
        idempotency_key="idem-abc",
    )
    assert result.created is True
    assert result.record.owner_user_id == "user-1"
    assert result.record.idempotency_key == "idem-abc"
    assert result.record.status == TaskStatus.PENDING

    # 第二次相同幂等键 -> 不新建
    again = svc.create_task(
        owner_user_id="user-1",
        raw_input="分析奶油巧克力",
        idempotency_key="idem-abc",
    )
    assert again.created is False
    assert again.record.task_id == result.record.task_id


def test_transition_enforces_state_machine(isolated_repo):
    svc = TaskService()
    result = svc.create_task(owner_user_id="u", raw_input="x")
    tid = result.record.task_id
    svc.transition(tid, TaskStatus.QUEUED)
    svc.transition(tid, TaskStatus.RUNNING)
    svc.transition(tid, TaskStatus.COMPLETED, progress=100)

    with pytest.raises(ValueError):
        svc.transition(tid, TaskStatus.RUNNING)  # COMPLETED 不可再转 RUNNING


def test_canvas_roundtrip(isolated_repo):
    svc = TaskService()
    result = svc.create_task(owner_user_id="u", raw_input="甜品", keywords=["甜品"])
    tid = result.record.task_id

    canvas = build_empty_canvas(task_id=tid, title="爆文洞察 · 甜品")
    # 4.3pre.3 新契约:画布核心模块改为 mod-viral-model-matrix(Sheet 2 矩阵)
    canvas.modules.append(
        CanvasModule(
            module_id="mod-viral-model-matrix",
            title="爆文模型矩阵",
            layer=1,
            status=ModuleStatus.READY,
            version=1,
        )
    )
    updated = svc.set_canvas(tid, canvas)
    assert updated.canvas_version == 1
    assert any(m.module_id == "mod-viral-model-matrix" for m in updated.modules)
