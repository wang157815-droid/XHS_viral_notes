"""
BaseAgent：节点抽象基类。

协议：
- 每个 Agent 声明 agent_id（用于 ModelGateway 路由）。
- 声明 provides（产出的 canvas module_id）与 depends_on（上游 module_id）。
- 执行入口 `async def run(context: AgentContext) -> AgentResult`。
- run() 内部只能：
    1) 从 TaskContext 读数据
    2) 通过 ModelGateway 调模型
    3) 写回自己的 TaskContext 分区
    4) 通过 EventBus 发 progress/log 事件
  禁止：节点间互调、直接 openai、直接访问 DB。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ...domain.task_context import TaskContext
from ...infrastructure.event_bus import task_event_bus
from ...infrastructure.execution import TaskExecutionHandle
from ...llm import model_gateway


@dataclass
class AgentContext:
    task_id: str
    task_context: TaskContext
    handle: Optional[TaskExecutionHandle] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    ok: bool
    produced_modules: List[str] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class BaseAgent:
    agent_id: str = "BaseAgent"
    provides: List[str] = []
    depends_on: List[str] = []
    write_partition: str = "input_spec"  # 子类覆盖

    def __init__(self, *, model_gateway_instance=model_gateway, event_bus=task_event_bus) -> None:
        self._gateway = model_gateway_instance
        self._bus = event_bus

    async def emit_progress(
        self,
        task_id: str,
        message: str,
        *,
        progress: Optional[int] = None,
        branch_id: Optional[str] = None,
    ) -> None:
        from ...domain.events import TaskEventType

        payload: Dict[str, Any] = {"agent_id": self.agent_id, "message": message}
        if progress is not None:
            payload["progress"] = progress
        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.AGENT_PROGRESS,
            payload=payload,
            branch_id=branch_id,
        )

    async def emit_log(
        self,
        task_id: str,
        level: str,
        message: str,
        *,
        branch_id: Optional[str] = None,
    ) -> None:
        from ...domain.events import TaskEventType

        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.LOG,
            payload={"level": level, "message": message, "agent_id": self.agent_id},
            branch_id=branch_id,
        )

    async def run(self, context: AgentContext) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError


AgentFactory = Callable[[], BaseAgent]
AgentRunner = Callable[[AgentContext], Awaitable[AgentResult]]
