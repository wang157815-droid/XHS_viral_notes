"""
编排引擎抽象层（阶段4.0）。

OrchestrationEngine 抽象层提供统一的 `start/cancel/snapshot` 入口。
通过 `get_orchestration_engine()` 工厂按 Feature Flag `ORCHESTRATION_ENGINE`
返回 SimpleEngine（默认）或 LangGraphEngine。

导出：
- OrchestrationEngine：抽象基类
- SimpleEngine / LangGraphEngine：双轨实现
- CheckpointStore / MemoryCheckpointStore：checkpoint 抽象
- get_orchestration_engine：工厂入口
"""

from .checkpoint_store import CheckpointStore, MemoryCheckpointStore
from .engine import OrchestrationEngine
from .factory import get_orchestration_engine, reset_engine_cache
from .simple_engine import SimpleEngine

__all__ = [
    "OrchestrationEngine",
    "SimpleEngine",
    "CheckpointStore",
    "MemoryCheckpointStore",
    "get_orchestration_engine",
    "reset_engine_cache",
]
