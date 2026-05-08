"""
模块状态机（6 态）。

- pending: 已登记但未开始生成
- generating: 正在生成（AI 调用中）
- ready: 生成完成，可展示
- stale: 上游变更导致需重生
- failed: 生成失败
- deleted: 已被用户删除
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet


class ModuleStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    DELETED = "deleted"


@dataclass(frozen=True)
class ModuleStatusTransition:
    source: ModuleStatus
    target: ModuleStatus


_ALLOWED: Dict[ModuleStatus, FrozenSet[ModuleStatus]] = {
    ModuleStatus.PENDING: frozenset(
        {ModuleStatus.GENERATING, ModuleStatus.FAILED, ModuleStatus.DELETED}
    ),
    ModuleStatus.GENERATING: frozenset(
        {ModuleStatus.READY, ModuleStatus.FAILED, ModuleStatus.DELETED}
    ),
    ModuleStatus.READY: frozenset(
        {ModuleStatus.GENERATING, ModuleStatus.STALE, ModuleStatus.DELETED}
    ),
    ModuleStatus.STALE: frozenset(
        {ModuleStatus.GENERATING, ModuleStatus.DELETED}
    ),
    ModuleStatus.FAILED: frozenset(
        {ModuleStatus.GENERATING, ModuleStatus.DELETED}
    ),
    ModuleStatus.DELETED: frozenset(
        {ModuleStatus.READY, ModuleStatus.GENERATING}
    ),
}


class ModuleStatusMachine:
    def allowed_next(self, status: ModuleStatus) -> FrozenSet[ModuleStatus]:
        return _ALLOWED.get(status, frozenset())

    def can_transition(self, source: ModuleStatus, target: ModuleStatus) -> bool:
        return target in self.allowed_next(source)

    def assert_transition(self, source: ModuleStatus, target: ModuleStatus) -> None:
        if not self.can_transition(source, target):
            raise ValueError(f"非法模块状态转换: {source.value} -> {target.value}")


module_status_machine = ModuleStatusMachine()
