"""Repository 层：任务状态读写隔离的存储抽象。"""

from .task_repository import TaskRecord, TaskRepository, task_repository

__all__ = ["TaskRecord", "TaskRepository", "task_repository"]
