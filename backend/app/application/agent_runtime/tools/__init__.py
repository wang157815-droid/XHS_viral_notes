"""Runtime 工具集合：把各能力模块注册进 ToolRegistry。

调用 register_default_tools(registry) 完成全部注册（幂等：override=True）。
"""

from __future__ import annotations

from loguru import logger

from ..tool_registry import ToolRegistry, tool_registry
from . import artifact, knowledge, memory, multimodal, notes, planning, report, web, workflows


_MODULES = [notes, memory, planning, artifact, knowledge, web, multimodal, report, workflows]


def register_default_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or tool_registry
    for module in _MODULES:
        try:
            module.register(reg)
        except ValueError:
            # 已注册（重复 import）时忽略
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("[agent_runtime.tools] 注册 {} 失败: {}", module.__name__, exc)
    logger.info("[agent_runtime.tools] 已注册工具: {}", reg.names())
    return reg


__all__ = ["register_default_tools"]
