"""
OrchestrationEngine 工厂（阶段4.0）。

读取 Feature Flag `ORCHESTRATION_ENGINE`：
- simple     -> SimpleEngine（默认，兼容自研 AgentOrchestrator）
- langgraph  -> LangGraphEngine（LangGraph 状态图）

实例按 engine name 缓存，reset_engine_cache() 用于测试清缓存。
"""

from __future__ import annotations

from threading import RLock
from typing import Dict

from ...core.feature_flags import get_orchestration_engine as _read_flag
from .engine import OrchestrationEngine
from .simple_engine import SimpleEngine


_lock = RLock()
_cache: Dict[str, OrchestrationEngine] = {}


def get_orchestration_engine() -> OrchestrationEngine:
    """按 Feature Flag 返回对应 Engine 单例。"""
    name = _read_flag()
    with _lock:
        cached = _cache.get(name)
        if cached is not None:
            return cached
        instance = _build(name)
        _cache[name] = instance
        return instance


def _build(name: str) -> OrchestrationEngine:
    if name == "langgraph":
        # 延迟导入：未安装 langgraph 时 simple 仍可用
        from .langgraph_engine import LangGraphEngine

        try:
            return LangGraphEngine()
        except Exception:
            # langgraph 依赖缺失或初始化失败 → 自动降级到 simple（但保留 simple 缓存键独立）
            return SimpleEngine()
    return SimpleEngine()


def reset_engine_cache() -> None:
    with _lock:
        _cache.clear()
