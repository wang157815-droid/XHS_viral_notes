"""
OrchestrationEngine：编排引擎抽象基类。

设计目标：
- 屏蔽「自研线性编排」和「LangGraph 状态图」的差异
- API 路由层只需依赖本抽象
- 阶段4.0 返回 `snapshot() == None`；阶段4.6 切 Postgres 后返回 checkpoint
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class OrchestrationEngine(ABC):
    """统一的任务编排引擎接口。"""

    name: str = "base"

    @abstractmethod
    async def start(self, task_id: str) -> None:
        """异步启动一个任务（不阻塞 API）。

        实现方负责：
        - 触发 agent 管线
        - 发布 SSE 事件（task_status / progress / ...）
        - 捕获致命错误并进入 failed 终态
        """

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """取消正在运行的任务。返回是否命中活跃任务。"""

    @abstractmethod
    async def snapshot(self, task_id: str) -> Optional[Dict[str, Any]]:
        """返回任务最新 checkpoint 快照。

        阶段4.0 可以返回 None（待 4.6 Postgres checkpoint 接入）。
        """
