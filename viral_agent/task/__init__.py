"""
任务管理模块

提供多任务支持：暂停、恢复、取消、持久化
"""

from .models import (
    TaskState,
    TaskCommand,
    TaskCheckpoint,
    TaskControlSignal,
    TaskModel,
)

from .persistence import (
    TaskPersistence,
    get_persistence,
)

from .manager import (
    TaskManager,
    get_task_manager,
)

__all__ = [
    # 数据模型
    "TaskState",
    "TaskCommand",
    "TaskCheckpoint",
    "TaskControlSignal",
    "TaskModel",
    # 持久化
    "TaskPersistence",
    "get_persistence",
    # 管理器
    "TaskManager",
    "get_task_manager",
]
