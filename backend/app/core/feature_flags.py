"""
Feature Flag 基础设施（阶段4.0 版）。

阶段4.0 只做 env 变量读取，支持通过 `.env` 或环境变量切换核心功能。
阶段4.12 会扩展为 env + DB 双层（数据库覆盖 + 用户白名单灰度），
此处结构预留扩展点，使用方直接 `get_flag(name, default)` 即可。

核心 flag：

- ORCHESTRATION_ENGINE: "simple" | "langgraph"
  控制任务编排走哪条引擎。simple 兼容原 AgentOrchestrator，
  langgraph 启用 LangGraph 状态图。
"""

from __future__ import annotations

import os
from typing import Optional


# 默认值
_DEFAULTS: dict[str, str] = {
    "ORCHESTRATION_ENGINE": "simple",
    "STORAGE_BACKEND": "json",
    "BACKLOG_BACKEND": "memory",
    "IDEMPOTENCY_BACKEND": "memory",
    "VIDEO_ANALYSIS_ENABLED": "true",
}


def get_flag(name: str, default: Optional[str] = None) -> str:
    """读取 Feature Flag。

    优先级：
    1. 环境变量（os.getenv）
    2. 内置默认值（_DEFAULTS）
    3. 调用方 default 参数

    阶段4.12 将在此插入"DB 覆盖 + 用户白名单"层，调用方接口不变。
    """
    env_val = os.getenv(name)
    if env_val is not None and env_val.strip():
        return env_val.strip()
    if name in _DEFAULTS:
        return _DEFAULTS[name]
    if default is not None:
        return default
    return ""


def get_flag_bool(name: str, default: bool = False) -> bool:
    """读取布尔 Feature Flag（'true'/'1'/'yes' 为 True）。"""
    raw = get_flag(name, "true" if default else "false").lower()
    return raw in ("true", "1", "yes", "on")


def get_orchestration_engine() -> str:
    """快捷函数：当前编排引擎名。"""
    value = get_flag("ORCHESTRATION_ENGINE", "simple").lower()
    if value not in ("simple", "langgraph"):
        return "simple"
    return value
