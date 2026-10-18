"""关键步骤确认闸（Human-in-the-loop gate）。

AgentRuntime 在执行 requires_confirmation=True 的工具前，向前端推送
agent_confirm_required 事件并在此登记一个待决确认；前端调用
POST /conversations/{id}/agent-confirmations/{confirmation_id} 回传用户决定。

- 待决确认只存在内存中（与流式会话同进程，流断开即失效）；
- resolve 校验归属用户 + 会话，防止越权代确认；
- 每次创建 / 决定都写审计日志。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4

from loguru import logger


@dataclass
class PendingConfirmation:
    confirmation_id: str
    owner_user_id: str
    conversation_id: str
    tool: str
    arguments: Dict[str, Any]
    future: "asyncio.Future[bool]"
    created_at: float = field(default_factory=time.time)


class ConfirmationBroker:
    def __init__(self) -> None:
        self._pending: Dict[str, PendingConfirmation] = {}

    def create(
        self, *, owner_user_id: str, conversation_id: str, tool: str, arguments: Dict[str, Any]
    ) -> PendingConfirmation:
        item = PendingConfirmation(
            confirmation_id=f"cfm_{uuid4().hex}",
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            tool=tool,
            arguments=dict(arguments or {}),
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[item.confirmation_id] = item
        logger.info(
            "[AgentConfirm] 创建 id={} tool={} user={} conv={}",
            item.confirmation_id, tool, owner_user_id, conversation_id,
        )
        return item

    def resolve(
        self, confirmation_id: str, *, approved: bool, owner_user_id: str, conversation_id: str
    ) -> bool:
        """返回 True 表示成功送达；不存在 / 已决 / 归属不符返回 False。"""
        item = self._pending.get(confirmation_id)
        if (
            item is None
            or item.future.done()
            or item.owner_user_id != owner_user_id
            or item.conversation_id != conversation_id
        ):
            return False
        item.future.set_result(bool(approved))
        logger.info(
            "[AgentConfirm] 决定 id={} tool={} approved={} user={}",
            confirmation_id, item.tool, approved, owner_user_id,
        )
        return True

    def discard(self, confirmation_id: str) -> None:
        item = self._pending.pop(confirmation_id, None)
        if item and not item.future.done():
            item.future.cancel()

    def get(self, confirmation_id: str) -> Optional[PendingConfirmation]:
        return self._pending.get(confirmation_id)


confirmation_broker = ConfirmationBroker()
