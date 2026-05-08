"""
CheckpointStore：编排状态持久化抽象。

阶段4.0：
- 定义接口 + 内存实现 MemoryCheckpointStore（与 LangGraph MemorySaver 并存）
- 不落盘，任务结束后数据可以丢

阶段4.6 会引入 SqlAlchemyCheckpointStore（Postgres）接管持久化。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Optional


@dataclass
class CheckpointSnapshot:
    task_id: str
    version: int = 0
    data: Dict[str, Any] = field(default_factory=dict)


class CheckpointStore(ABC):
    @abstractmethod
    def save(self, task_id: str, data: Dict[str, Any]) -> CheckpointSnapshot: ...

    @abstractmethod
    def load(self, task_id: str) -> Optional[CheckpointSnapshot]: ...

    @abstractmethod
    def delete(self, task_id: str) -> None: ...


class MemoryCheckpointStore(CheckpointStore):
    """阶段4.0 默认内存实现。"""

    def __init__(self) -> None:
        self._entries: Dict[str, CheckpointSnapshot] = {}
        self._lock = RLock()

    def save(self, task_id: str, data: Dict[str, Any]) -> CheckpointSnapshot:
        with self._lock:
            prev = self._entries.get(task_id)
            version = (prev.version if prev else 0) + 1
            snap = CheckpointSnapshot(task_id=task_id, version=version, data=dict(data))
            self._entries[task_id] = snap
            return snap

    def load(self, task_id: str) -> Optional[CheckpointSnapshot]:
        with self._lock:
            snap = self._entries.get(task_id)
            if not snap:
                return None
            return CheckpointSnapshot(
                task_id=snap.task_id,
                version=snap.version,
                data=dict(snap.data),
            )

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._entries.pop(task_id, None)


memory_checkpoint_store = MemoryCheckpointStore()
