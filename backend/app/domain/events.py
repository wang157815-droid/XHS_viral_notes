"""
SSE 事件契约。

每个事件包含：
- event_id: 全局唯一 UUID（用于前端去重/Last-Event-ID 回放）
- sequence_id: 任务内严格单调递增序列
- branch_id: 分支标识（用于并行分支追踪）
- type: 事件类型（TaskEventType）
- task_id: 所属任务
- timestamp: ISO8601
- payload: 事件数据
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Dict, Optional


class TaskEventType(str, Enum):
    PING = "ping"
    TASK_STATUS = "task_status"
    AGENT_PROGRESS = "agent_progress"
    LOG = "log"
    CANVAS_MODULE_UPDATED = "canvas_module_updated"
    CANVAS_SCHEMA_UPDATED = "canvas_schema_updated"
    ERROR = "error"
    DONE = "done"
    # 视频异步分析支路结束(阶段 4.2)：主任务 DONE 后,视频模块还在后台分析;
    # 分析收尾后发此事件,前端 SSE 客户端据此关闭保活连接。
    TASK_VIDEO_DONE = "task_video_done"
    # LLM 流式推理片段（按句子推送，支持 <think> 推理链提取）
    AGENT_THINKING_CHUNK = "agent_thinking_chunk"
    # 某个 Agent 的流式推理结束
    AGENT_THINKING_DONE = "agent_thinking_done"
    # 后台任务完成后回写的对话消息（评论分析等 Skill 专用）
    CONVERSATION_MESSAGE = "conversation_message"


@dataclass
class TaskEvent:
    event_id: str
    sequence_id: int
    type: TaskEventType
    task_id: str
    timestamp: str
    payload: Dict[str, Any] = field(default_factory=dict)
    branch_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence_id": self.sequence_id,
            "type": self.type.value,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "branch_id": self.branch_id,
            "payload": self.payload,
        }


class TaskEventFactory:
    """线程安全的事件工厂，按 task_id 维护 sequence_id。"""

    def __init__(self) -> None:
        self._sequences: Dict[str, itertools.count] = {}
        self._lock = Lock()

    def _next_seq(self, task_id: str) -> int:
        with self._lock:
            counter = self._sequences.setdefault(task_id, itertools.count(1))
            return next(counter)

    def reset(self, task_id: str) -> None:
        with self._lock:
            self._sequences.pop(task_id, None)

    def create(
        self,
        *,
        task_id: str,
        type: TaskEventType,
        payload: Optional[Dict[str, Any]] = None,
        branch_id: Optional[str] = None,
    ) -> TaskEvent:
        return TaskEvent(
            event_id=uuid.uuid4().hex,
            sequence_id=self._next_seq(task_id),
            type=type,
            task_id=task_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
            branch_id=branch_id,
        )


task_event_factory = TaskEventFactory()
