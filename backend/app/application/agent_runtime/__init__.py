"""agent_runtime —— 自主规划 Agent 运行时（与固定 workflow 并存的第二条执行路径）。

对外暴露：
- tool_registry / ToolRegistry / ToolSpec / ToolContext / ToolResult
- AgentRuntime / AgentRuntimeConfig / AgentRunTrace / agent_runtime
- register_default_tools / get_default_runtime

默认工具在首次构建 runtime 时惰性注册，避免 import 期触发重型依赖。
"""

from __future__ import annotations

from typing import Optional

from .runtime import AgentRunTrace, AgentRuntime, AgentRuntimeConfig, agent_runtime
from .tool_registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    tool_registry,
)

_tools_registered = False


def ensure_default_tools() -> ToolRegistry:
    """惰性注册默认工具（幂等）。"""
    global _tools_registered
    if not _tools_registered:
        from .tools import register_default_tools

        register_default_tools(tool_registry)
        _tools_registered = True
    return tool_registry


def get_default_runtime() -> AgentRuntime:
    """返回已注册默认工具的全局 runtime。"""
    ensure_default_tools()
    return agent_runtime


def build_runtime(config: Optional[AgentRuntimeConfig] = None) -> AgentRuntime:
    """按 Settings 中的护栏参数构建一个 AgentRuntime（共享全局工具注册表）。"""
    ensure_default_tools()
    if config is None:
        from ...core.config import settings

        config = AgentRuntimeConfig(
            max_iters=settings.agent_runtime_max_iters,
            tool_budget=settings.agent_runtime_tool_budget,
            max_expensive_calls=settings.agent_runtime_max_expensive_calls,
            expensive_unit_budget=settings.agent_runtime_expensive_unit_budget,
            per_tool_timeout=settings.agent_runtime_per_tool_timeout,
            per_tool_timeout_cheap=settings.agent_runtime_per_tool_timeout_cheap,
            per_tool_timeout_expensive=settings.agent_runtime_per_tool_timeout_expensive,
        )
    return AgentRuntime(registry=tool_registry, config=config)


__all__ = [
    "AgentRunTrace",
    "AgentRuntime",
    "AgentRuntimeConfig",
    "agent_runtime",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "tool_registry",
    "ensure_default_tools",
    "get_default_runtime",
    "build_runtime",
]
