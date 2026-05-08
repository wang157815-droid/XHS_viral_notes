"""
TaskService：任务用例层。

职责：
- 创建任务（幂等）
- 状态转换（走任务状态机）
- 暂停/恢复/取消
- 任务列表与详情
- Canvas 获取/更新
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from loguru import logger

from ..domain.canvas import CanvasSchema, build_empty_canvas
from ..domain.error_codes import ErrorCode, build_error
from ..domain.events import TaskEventType, task_event_factory
from ..domain.task_context import task_context_store
from ..domain.task_status import TaskStatus, task_status_machine
from ..infrastructure.repository import TaskRecord, task_repository
from ..core.tracing import get_trace_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CreateTaskResult:
    record: TaskRecord
    created: bool  # False 表示命中幂等返回已有任务
    canvas: CanvasSchema


class TaskService:
    """阶段2任务服务（不直接操作 DB，走 Repository 与 ContextStore）。"""

    def __init__(self) -> None:
        self._canvas: Dict[str, CanvasSchema] = {}
        self._canvas_lock = RLock()

    # ------------------------------------------------------------------
    # 创建与查询
    # ------------------------------------------------------------------
    def create_task(
        self,
        *,
        owner_user_id: str,
        raw_input: str,
        keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
        advanced_config: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> CreateTaskResult:
        if idempotency_key:
            existing = task_repository.find_by_idempotency(idempotency_key)
            if existing:
                canvas = self._get_or_init_canvas(existing)
                return CreateTaskResult(record=existing, created=False, canvas=canvas)

        task_id = self._generate_task_id()
        comp_kw = [str(x).strip() for x in (competitor_keywords or []) if str(x).strip()]
        record = TaskRecord(
            task_id=task_id,
            owner_user_id=owner_user_id,
            status=TaskStatus.PENDING,
            input_spec={
                "raw_input": raw_input,
                "keywords": keywords or [],
                "competitor_keywords": comp_kw,
                "advanced_config": advanced_config or {},
            "_trace_id": get_trace_id(),
            },
            idempotency_key=idempotency_key,
            keywords=keywords or [],
        )
        task_repository.create(record)
        task_context_store.create(task_id, input_spec=record.input_spec)
        canvas = self._init_canvas(record)

        logger.info(
            "[TaskService] 任务创建 task_id={} owner={} idem={}",
            task_id,
            owner_user_id,
            idempotency_key,
        )
        return CreateTaskResult(record=record, created=True, canvas=canvas)

    def get_task(self, task_id: str) -> TaskRecord:
        return task_repository.require(task_id)

    def list_tasks(self, owner_user_id: str, *, include_all: bool, limit: int = 50) -> List[TaskRecord]:
        return task_repository.list_for_user(owner_user_id, include_all=include_all, limit=limit)

    # ------------------------------------------------------------------
    # 状态转换
    # ------------------------------------------------------------------
    def transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        error_code: Optional[str] = None,
        last_error: Optional[str] = None,
        progress: Optional[int] = None,
    ) -> TaskRecord:
        record = task_repository.require(task_id)
        task_status_machine.assert_transition(record.status, target)
        changes: Dict[str, Any] = {"status": target}
        if progress is not None:
            changes["progress"] = max(0, min(100, int(progress)))
        if target == TaskStatus.FAILED:
            changes["error_code"] = error_code or ErrorCode.SYSTEM_INTERNAL.value
            changes["last_error"] = last_error
        if target in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
            changes["progress"] = 100 if target == TaskStatus.COMPLETED else record.progress
        updated = task_repository.update(task_id, **changes)
        return updated

    def pause(self, task_id: str) -> TaskRecord:
        return self.transition(task_id, TaskStatus.PAUSED)

    def resume(self, task_id: str) -> TaskRecord:
        return self.transition(task_id, TaskStatus.RUNNING)

    def cancel(self, task_id: str) -> TaskRecord:
        return self.transition(task_id, TaskStatus.CANCELLED)

    # ------------------------------------------------------------------
    # Canvas
    # ------------------------------------------------------------------
    def get_canvas(self, task_id: str) -> CanvasSchema:
        record = task_repository.require(task_id)
        return self._get_or_init_canvas(record)

    def set_canvas(self, task_id: str, canvas: CanvasSchema) -> CanvasSchema:
        with self._canvas_lock:
            canvas.bump_version()
            self._canvas[task_id] = canvas
        task_repository.update(task_id, canvas_version=canvas.canvas_version)
        writer = task_context_store.writer(task_id)
        writer.write("canvas_document", canvas.to_dict(), agent_id="TaskService", note="canvas_updated")
        return canvas

    def update_module_content(
        self,
        task_id: str,
        module_id: str,
        *,
        content: Optional[Dict[str, Any]] = None,
        status: Optional[Any] = None,
        summary: Optional[str] = None,
        agent_id: str = "TaskService",
    ) -> Optional[Dict[str, Any]]:
        """更新持久化 canvas 上单个模块(供异步支路写回用,如视频分析)。

        Args:
            task_id:    任务 id
            module_id:  目标模块 id (如 `mod-video-analysis`)
            content:    新的 content dict;None 表示不改
            status:     新的 ModuleStatus(或字符串);None 表示不改
            summary:    新的 summary 文本;None 表示不改
            agent_id:   写入审计用的 agent 标识

        Returns:
            更新后该模块的 to_dict() 字典;找不到模块时返回 None。
            返回值结构已经适配 SSE `canvas_module_updated` payload 需求。
        """
        with self._canvas_lock:
            canvas = self._canvas.get(task_id)
            if canvas is None:
                logger.warning(
                    "[TaskService.update_module_content] 未初始化 canvas,跳过 task_id={} module_id={}",
                    task_id,
                    module_id,
                )
                return None
            module = canvas.find_module(module_id)
            if module is None:
                logger.warning(
                    "[TaskService.update_module_content] 模块不存在 task_id={} module_id={}",
                    task_id,
                    module_id,
                )
                return None

            if content is not None:
                module.content = content
            if summary is not None:
                module.summary = summary
            if status is not None:
                # 允许传 ModuleStatus 或字符串值
                from ..domain.module_status import ModuleStatus as _MS

                if isinstance(status, _MS):
                    module.status = status
                else:
                    try:
                        module.status = _MS(str(status))
                    except ValueError:
                        logger.warning(
                            "[TaskService.update_module_content] 非法 status={} 忽略",
                            status,
                        )
            module.version += 1
            canvas.bump_version()
            updated_snapshot = module.to_dict()
            canvas_version = canvas.canvas_version

        task_repository.update(task_id, canvas_version=canvas_version)
        try:
            writer = task_context_store.writer(task_id)
            writer.write(
                "canvas_document",
                canvas.to_dict(),
                agent_id=agent_id,
                note=f"module_patch:{module_id}",
            )
        except Exception as exc:  # noqa: BLE001 - 审计写失败不能影响画布更新
            logger.debug(
                "[TaskService.update_module_content] 审计写失败 task_id={}: {}",
                task_id,
                exc,
            )
        return updated_snapshot

    def _get_or_init_canvas(self, record: TaskRecord) -> CanvasSchema:
        with self._canvas_lock:
            cached = self._canvas.get(record.task_id)
            if cached:
                return cached
        # 内存未命中：尝试从 TaskContext.canvas_document 恢复（重启后）
        try:
            ctx = task_context_store.get(record.task_id)
            if ctx is not None:
                canvas_doc = ctx.get("canvas_document")
                if canvas_doc:
                    restored = CanvasSchema.from_dict(canvas_doc)
                    with self._canvas_lock:
                        self._canvas[record.task_id] = restored
                    logger.info(
                        "[TaskService] 从 TaskContext 恢复画布 task_id={} version={}",
                        record.task_id,
                        restored.canvas_version,
                    )
                    return restored
        except Exception as exc:
            logger.warning(
                "[TaskService] 从 TaskContext 恢复画布失败 task_id={}: {}",
                record.task_id,
                exc,
            )
        return self._init_canvas(record)

    def _init_canvas(self, record: TaskRecord) -> CanvasSchema:
        raw_input = (record.input_spec or {}).get("raw_input") or ""
        title = f"爆文洞察与框架 · {raw_input[:20] or record.task_id}"
        canvas = build_empty_canvas(task_id=record.task_id, title=title)
        with self._canvas_lock:
            self._canvas[record.task_id] = canvas
        return canvas

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_task_id() -> str:
        from uuid import uuid4

        return f"task_{uuid4().hex[:12]}"

    def build_event_summary(self, record: TaskRecord) -> Dict[str, Any]:
        return {
            "task_id": record.task_id,
            "status": record.status.value,
            "progress": record.progress,
            "canvas_version": record.canvas_version,
            "context_version": record.context_version,
            "updated_at": record.updated_at,
        }


task_service = TaskService()


# 工厂以便后续可替换
def get_event_factory():
    return task_event_factory


def now_iso() -> str:
    return _utc_now()


__all__ = [
    "TaskService",
    "task_service",
    "CreateTaskResult",
    "get_event_factory",
    "now_iso",
]
