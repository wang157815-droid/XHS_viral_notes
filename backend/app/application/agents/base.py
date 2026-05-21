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

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

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


# 句子边界正则：中英文标点 + 换行；缓冲区满 sentence_min_chars 也强制切分
_SENTENCE_END_RE = re.compile(r"[。！？\n\r]|[.!?]\s")


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

    async def _emit_thinking_chunk(
        self, task_id: str, chunk: str, *, is_reasoning: bool = False
    ) -> None:
        """发送单条 AGENT_THINKING_CHUNK 事件。"""
        from ...domain.events import TaskEventType

        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.AGENT_THINKING_CHUNK,
            payload={
                "agent_id": self.agent_id,
                "chunk": chunk,
                "is_reasoning": is_reasoning,
            },
        )

    async def _emit_thinking_done(self, task_id: str) -> None:
        """发送 AGENT_THINKING_DONE 事件，停止思考光标闪烁。"""
        from ...domain.events import TaskEventType

        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.AGENT_THINKING_DONE,
            payload={"agent_id": self.agent_id},
        )

    async def chat_stream_and_emit(
        self,
        task_id: str,
        messages: List[Dict[str, Any]],
        *,
        modality: str = "text",
        overrides: Optional[Dict[str, Any]] = None,
        sentence_min_chars: int = 40,
    ) -> str:
        """流式调用 LLM，实时发 AGENT_THINKING_CHUNK 事件。

        内容处理策略：
        1. <think>...</think> 推理链（DeepSeek R1 / Qwen3）：实时按句子推送，is_reasoning=True
        2. </think> 之后的 JSON 主体：不流出
        3. 无 <think> 标签时：实时流式推送自然语言内容（is_reasoning=False），
           检测到「行首 JSON 块」（以 { [ ``` 开头的新行）时自动停止，避免 JSON 噪音
        4. streaming 异常自动 fallback 到 _gateway.chat()

        返回完整文本供调用方解析 JSON。
        """
        from ...domain.events import TaskEventType

        full_text = ""
        think_buf = ""      # <think>...</think> 内容（实时流）
        non_think_buf = ""  # 非 think 内容（实时流，遇 JSON 行停止）
        in_think = False
        think_done = False
        json_started = False  # 检测到 JSON 块，停止非 think 内容的流出

        async def _flush_think(force: bool = False) -> None:
            nonlocal think_buf
            if not think_buf:
                return
            if force or len(think_buf) >= sentence_min_chars or _SENTENCE_END_RE.search(think_buf):
                await self._emit_thinking_chunk(task_id, think_buf, is_reasoning=True)
                think_buf = ""

        async def _process_non_think(delta: str) -> None:
            """实时处理非 think 内容：按句子推送，遇到 JSON 块行首自动停止。"""
            nonlocal non_think_buf, json_started
            if json_started:
                return

            non_think_buf += delta

            # 检测整体是否以 JSON 块开头（模型直接输出 JSON，无自然语言前言）
            stripped_all = non_think_buf.lstrip("\n\r ")
            if stripped_all.startswith(("{", "[", "```")):
                json_started = True
                non_think_buf = ""
                return

            # 检测最后一行是否是 JSON 块开始（自然语言 + 换行 + JSON）
            last_nl = non_think_buf.rfind("\n")
            if last_nl >= 0:
                last_line = non_think_buf[last_nl + 1:].strip()
                if last_line.startswith(("{", "[", "```")):
                    json_started = True
                    # 刷出换行前的自然语言内容
                    pre = non_think_buf[:last_nl].strip()
                    if pre:
                        await self._emit_thinking_chunk(task_id, pre, is_reasoning=False)
                    non_think_buf = ""
                    return

            # 按句子边界实时推送
            if len(non_think_buf) >= sentence_min_chars or _SENTENCE_END_RE.search(non_think_buf):
                await self._emit_thinking_chunk(task_id, non_think_buf, is_reasoning=False)
                non_think_buf = ""

        try:
            async for event in self._gateway.chat_stream(
                self.agent_id,
                messages,
                modality=modality,
                task_id=task_id,
                overrides=overrides,
            ):
                etype = event.get("type")
                if etype == "message_delta":
                    delta: str = event.get("delta") or ""
                    full_text += delta

                    remaining = delta
                    while remaining:
                        if not in_think and not think_done:
                            think_start = remaining.find("<think>")
                            if think_start != -1:
                                # <think> 前的内容实时流
                                await _process_non_think(remaining[:think_start])
                                in_think = True
                                remaining = remaining[think_start + len("<think>"):]
                            else:
                                await _process_non_think(remaining)
                                break
                        elif in_think:
                            think_end = remaining.find("</think>")
                            if think_end != -1:
                                think_buf += remaining[:think_end]
                                await _flush_think(force=True)
                                in_think = False
                                think_done = True
                                # </think> 后的 JSON 主体不流出
                                break
                            else:
                                think_buf += remaining
                                await _flush_think()
                                break
                        else:
                            # think_done=True：</think> 后的 JSON，忽略
                            break

                elif etype == "message_done":
                    full_text = event.get("content") or full_text

        except Exception:
            # streaming 异常：fallback 到同步 chat
            try:
                resp = await self._gateway.chat(
                    self.agent_id, messages,
                    modality=modality, task_id=task_id, overrides=overrides,
                )
                full_text = (resp.get("content") or "").strip()
            except Exception:
                pass

        # 刷出最后 think 缓冲
        await _flush_think(force=True)

        # 刷出最后 non_think 缓冲（非 JSON 部分）
        if non_think_buf.strip() and not json_started:
            sline = non_think_buf.strip()
            if not sline.startswith(("{", "[", "```")):
                await self._emit_thinking_chunk(task_id, sline, is_reasoning=False)

        # 通知前端该 Agent 推理结束
        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.AGENT_THINKING_DONE,
            payload={"agent_id": self.agent_id},
        )

        # ── 返回前剥离 <think>...</think> 推理链 ──────────────────────────────
        # 调用方（所有 Agent JSON 解析）只需处理正式输出，不应看到 <think> 块。
        # 优先用字符串 split（比 regex 更可靠，不受思考内容中偶发 </think> 干扰）。
        if "</think>" in full_text:
            after = full_text.split("</think>", 1)[1].strip()
            if after:
                return after
            # 模型只输出了思考块、正文为空（deepseek 偶发）：
            # 把完整原文返回给调用方，让它做最后处理（如重试或兜底）
            logger.warning(
                "[chat_stream_and_emit] </think> 后正文为空，返回完整原文供调用方处理"
            )
            return full_text
        if "<think>" in full_text:
            # 思考块未关闭（截断场景）：尝试剥除 <think> 之后的全部内容
            stripped = re.sub(r"<think>[\s\S]*", "", full_text, flags=re.IGNORECASE).strip()
            if stripped:
                return stripped
        return full_text

    async def run(self, context: AgentContext) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError


AgentFactory = Callable[[], BaseAgent]
AgentRunner = Callable[[AgentContext], Awaitable[AgentResult]]


def _extract_pre_json_text(text: str) -> str:
    """从 LLM 响应中提取 JSON 块之前的自然语言摘要部分。

    - 如果响应以 `{` / `[` / ` ``` ` 开头（纯 JSON），返回空字符串
    - 否则返回第一个 JSON 块（以 `{` / `[` / ` ``` ` 行开头）之前的所有文字
    - 供 chat_stream_and_emit 将模型的自然语言思考摘要展示到思考框
    """
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[0] in ('{', '[', '`'):
        return ""
    lines = stripped.split('\n')
    preamble_lines: List[str] = []
    for line in lines:
        sline = line.strip()
        if sline.startswith('{') or sline.startswith('[') or sline.startswith('```'):
            break
        preamble_lines.append(line)
    return '\n'.join(preamble_lines).strip()
