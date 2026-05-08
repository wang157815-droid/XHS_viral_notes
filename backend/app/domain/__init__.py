"""
Domain 层：契约与状态机。

- 任务状态机：TaskStatus + 转换表
- 模块状态机：ModuleStatus + 转换表
- 错误码：ErrorCode（5 大类）
- SSE 事件契约：TaskEvent
- TaskContext：状态载体
- CanvasSchema：画布契约
- ViralNote / ViralModel: 4.3pre.1 新增的爆款笔记 + 爆文模型矩阵契约
"""

from .task_status import TaskStatus, TaskStatusTransition, task_status_machine
from .module_status import ModuleStatus, ModuleStatusTransition, module_status_machine
from .error_codes import ErrorCategory, ErrorCode, build_error, raise_api_error
from .events import TaskEventType, TaskEvent, TaskEventFactory
from .viral_note import SourceType, ViralNote
from .viral_model import (
    ELEMENT_ORDER,
    ElementCategory,
    ElementCode,
    ElementExample,
    UnusedDirection,
    ViralModel,
    ViralModelMatrix,
)

__all__ = [
    "TaskStatus",
    "TaskStatusTransition",
    "task_status_machine",
    "ModuleStatus",
    "ModuleStatusTransition",
    "module_status_machine",
    "ErrorCategory",
    "ErrorCode",
    "build_error",
    "raise_api_error",
    "TaskEventType",
    "TaskEvent",
    "TaskEventFactory",
    # 4.3pre.1 新增
    "SourceType",
    "ViralNote",
    "ElementCode",
    "ELEMENT_ORDER",
    "ElementExample",
    "ElementCategory",
    "ViralModel",
    "UnusedDirection",
    "ViralModelMatrix",
]
