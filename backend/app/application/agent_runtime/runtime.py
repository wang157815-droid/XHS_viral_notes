"""AgentRuntime —— 原生 function-calling loop 执行器。

范式：每一轮用 model_gateway.chat_with_tools() 让模型决定"调用工具"还是"给出最终答案"。
工具执行结果以 user 消息形式回注（对 DeepSeek/GLM/Qwen 等是否原生支持 tools 都鲁棒，
也兼容 gateway 的 JSON fallback 路径），直到模型不再请求工具或触发预算/步数上限。

护栏：
- max_iters：最大规划轮数；
- tool_budget：单次运行内工具调用总次数硬上限；
- max_expensive_calls：昂贵工具（采集类）硬上限（gate 的非交互式实现）；
- per_tool_timeout：单工具超时。

输出：run_stream() 以事件流形式产出 —— 规划/工具调用/观察作为 status 事件，
最终答案用 chat_stream 流式（natural finish 时直接复用已生成内容做流式，省一次调用）。
token/cost 由 gateway 内部审计写入 metrics_store。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from ...llm.model_gateway import ModelInvocationError, model_gateway
from .prompts import PLANNER_SYSTEM_PROMPT, build_goal_prompt
from .tool_registry import ToolContext, ToolRegistry, ToolResult, tool_registry


_OBS_MAX_CHARS = 4000  # 单个工具观察回注模型的最大字符数（防止 context 爆炸）
# natural-finish 复用已生成内容时的"打字机"合成流式参数：
# 固定分片数 + 真实小延时，保证前端逐步渲染（asyncio.sleep(0) 不产生真实间隔会被一次性 flush）。
_REPLAY_TARGET_CHUNKS = 50   # 目标分片数（分片大小随文本长度自适应）
_REPLAY_MIN_CHUNK = 12       # 单分片最小字符数（短文本不至于切太碎）
_REPLAY_DELAY = 0.025        # 每片之间的真实延时（秒）→ 总时长约 1.25s


@dataclass
class AgentRuntimeConfig:
    agent_id: str = "PlannerAgent"
    max_iters: int = 8
    tool_budget: int = 16
    # 昂贵工具的两道闸：
    # - max_expensive_calls：昂贵工具"调用次数"上限（批量工具算 1 次）；
    # - expensive_unit_budget：昂贵工具消耗的"单位"（≈上游 API 请求数）总预算。
    #   批量工具（collect_notes/fetch_notes_details）一次调用消耗多单位，
    #   单位制让"逐条拆解 N 篇"不再被按次数的硬上限误伤。
    max_expensive_calls: int = 12
    expensive_unit_budget: int = 60
    # 单工具超时：按 ToolSpec.cost 分级取值，未匹配时回退 per_tool_timeout。
    per_tool_timeout: float = 120.0
    per_tool_timeout_cheap: float = 60.0
    per_tool_timeout_expensive: float = 200.0
    final_max_tokens: int = 1800
    history_messages: int = 6

    def timeout_for(self, cost: Optional[str]) -> float:
        if cost == "expensive":
            return self.per_tool_timeout_expensive
        if cost == "cheap":
            return self.per_tool_timeout_cheap
        return self.per_tool_timeout


@dataclass
class AgentRunTrace:
    """一次运行的可观测轨迹（供测试 / 调试 / 审计）。"""

    final_content: str = ""
    iterations: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    error: Optional[str] = None


class AgentRuntime:
    def __init__(
        self,
        *,
        registry: Optional[ToolRegistry] = None,
        gateway: Any = None,
        config: Optional[AgentRuntimeConfig] = None,
    ) -> None:
        self.registry = registry or tool_registry
        self.gateway = gateway or model_gateway
        self.config = config or AgentRuntimeConfig()

    # ── 流式执行 ──────────────────────────────────────────────────────────
    async def run_stream(
        self,
        *,
        goal: str,
        ctx: ToolContext,
        history: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        trace: Optional[AgentRunTrace] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        cfg = self.config
        trace = trace if trace is not None else AgentRunTrace()
        task_id = ctx.task_id or f"agent:{ctx.conversation_id or 'anon'}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt or PLANNER_SYSTEM_PROMPT}
        ]
        for m in (history or [])[-cfg.history_messages:]:
            role = m.get("role")
            text = str(m.get("content") or "").strip()
            if role in {"user", "assistant"} and text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": build_goal_prompt(goal, advanced_config=ctx.advanced_config)})

        tools = self.registry.schemas()
        tool_calls_used = 0
        expensive_calls = 0
        expensive_units = 0
        final_content = ""
        forced_final = False

        for iteration in range(1, cfg.max_iters + 1):
            trace.iterations = iteration
            yield {
                "type": "status",
                "status": "agent_planning",
                "message": f"正在规划第 {iteration} 步...",
                "iteration": iteration,
            }
            logger.info(
                "[AgentRuntime] task={} 第 {}/{} 轮规划开始（已用工具 {}/{}，昂贵调用 {}/{}，昂贵单位 {}/{}）",
                task_id, iteration, cfg.max_iters,
                tool_calls_used, cfg.tool_budget,
                expensive_calls, cfg.max_expensive_calls,
                expensive_units, cfg.expensive_unit_budget,
            )
            plan_start = time.monotonic()
            try:
                resp = await self.gateway.chat_with_tools(
                    cfg.agent_id,
                    messages,
                    tools=tools,
                    tool_choice="auto",
                    task_id=task_id,
                )
            except ModelInvocationError as exc:
                logger.warning("[AgentRuntime] chat_with_tools 失败: {} {}", exc.code, exc)
                trace.error = exc.code
                trace.stop_reason = "model_error"
                async for ev in self._emit_text_as_stream(
                    f"抱歉，自主分析过程中模型调用失败（{exc.code}）。请稍后重试或调整配置。"
                ):
                    yield ev
                return

            tool_calls = resp.get("tool_calls") or []
            assistant_text = str(resp.get("content") or "").strip()
            plan_ms = int((time.monotonic() - plan_start) * 1000)
            logger.info(
                "[AgentRuntime] task={} 第 {} 轮规划完成 ms={} 决策={}",
                task_id, iteration, plan_ms,
                ("最终答案" if not tool_calls else "、".join(str(c.get("name")) for c in tool_calls)),
            )

            if not tool_calls:
                final_content = assistant_text
                trace.stop_reason = "model_final"
                break

            # 记录模型本轮决策（以文本形式入历史，保证 provider 无关的鲁棒性）
            decided = "；".join(
                f"{c.get('name')}({self._short_args(c.get('arguments'))})" for c in tool_calls
            )
            messages.append(
                {"role": "assistant", "content": assistant_text or f"[决定调用工具] {decided}"}
            )

            observations: List[str] = []
            for call in tool_calls:
                name = str(call.get("name") or "")
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}

                if tool_calls_used >= cfg.tool_budget:
                    observations.append(f"[{name}] 已达工具调用预算上限（{cfg.tool_budget}），本次跳过。")
                    forced_final = True
                    continue

                spec = self.registry.get(name)
                if spec and spec.cost == "expensive":
                    if expensive_calls >= cfg.max_expensive_calls:
                        observations.append(
                            f"[{name}] 已达昂贵采集调用次数上限（{cfg.max_expensive_calls}），"
                            "请基于已获取的工作记忆数据继续分析（可用 query_dataset）。"
                        )
                        continue
                    if expensive_units >= cfg.expensive_unit_budget:
                        observations.append(
                            f"[{name}] 已达昂贵采集单位预算上限（{cfg.expensive_unit_budget}），"
                            "请基于已获取数据继续分析，不要再采集。"
                        )
                        continue

                yield {
                    "type": "status",
                    "status": "agent_tool_call",
                    "tool": name,
                    "arguments": args,
                    "iteration": iteration,
                }
                tool_timeout = cfg.timeout_for(spec.cost if spec else None)
                logger.info(
                    "[AgentRuntime] task={} 调用工具 {} (cost={}, timeout={:.0f}s) args={}",
                    task_id, name, (spec.cost if spec else "?"), tool_timeout, self._short_args(args),
                )
                tool_start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        self.registry.execute(name, args, ctx),
                        timeout=tool_timeout,
                    )
                except asyncio.TimeoutError:
                    result = ToolResult(
                        ok=False,
                        content=f"工具 {name} 执行超时（>{tool_timeout:.0f}s）",
                        error="TIMEOUT",
                    )
                tool_ms = int((time.monotonic() - tool_start) * 1000)
                tool_calls_used += 1
                if spec and spec.cost == "expensive":
                    expensive_calls += 1
                    expensive_units += max(int(getattr(result, "units", 1) or 0), 0)
                trace.tool_calls.append({"name": name, "ok": result.ok, "arguments": args})
                logger.info(
                    "[AgentRuntime] task={} 工具 {} 完成 ms={} ok={} units={}{}",
                    task_id, name, tool_ms, result.ok, getattr(result, "units", 1),
                    "" if result.ok else f" error={result.error}",
                )

                yield {
                    "type": "status",
                    "status": "agent_tool_result",
                    "tool": name,
                    "ok": result.ok,
                    "summary": (result.display or result.error or "完成")[:200],
                    "iteration": iteration,
                }

                # 计划 / 产物：把工具产生的结构化副产物作为专用事件外发，驱动前端可视化
                if result.ok and result.data:
                    if name in {"set_plan", "update_plan"} and result.data.get("plan") is not None:
                        yield {
                            "type": "status",
                            "status": "agent_plan",
                            "plan": result.data.get("plan"),
                            "iteration": iteration,
                        }
                    elif name == "present_artifact" and result.data.get("artifact"):
                        yield {
                            "type": "artifact",
                            "artifact": result.data.get("artifact"),
                            "iteration": iteration,
                        }

                observations.append(f"[{name}] 结果：\n{self._truncate(result.content)}")

            messages.append(
                {
                    "role": "user",
                    "content": (
                        "工具执行结果如下：\n\n"
                        + "\n\n".join(observations)
                        + "\n\n请基于以上真实结果继续：若信息不足可继续调用工具，"
                        "否则直接给出最终中文 Markdown 回答。"
                    ),
                }
            )
            if forced_final:
                break
        else:
            # for 正常跑满 max_iters（未 break）
            forced_final = True
            trace.stop_reason = "max_iters"

        if final_content and not forced_final:
            trace.final_content = final_content
            logger.info(
                "[AgentRuntime] task={} 结束 stop_reason={} 轮数={} 工具调用={}（natural finish）",
                task_id, trace.stop_reason, trace.iterations, len(trace.tool_calls),
            )
            async for ev in self._emit_text_as_stream(final_content):
                yield ev
            return

        # 触发强制收尾（预算/步数耗尽，或 natural-final 但内容为空）：用 chat_stream 生成最终答案
        if not trace.stop_reason:
            trace.stop_reason = "forced_final"
        logger.info(
            "[AgentRuntime] task={} 强制收尾 stop_reason={} 轮数={} 工具调用={}",
            task_id, trace.stop_reason, trace.iterations, len(trace.tool_calls),
        )
        messages.append(
            {
                "role": "user",
                "content": "请立即基于上述已获取的真实信息，给出最终的中文 Markdown 回答，不要再请求任何工具。",
            }
        )
        collected: List[str] = []
        async for ev in self._stream_final_answer(messages, task_id=task_id):
            if ev.get("type") == "message_delta":
                collected.append(str(ev.get("delta") or ""))
            yield ev
        trace.final_content = "".join(collected).strip() or final_content

    # ── 非流式便捷入口（测试用）─────────────────────────────────────────────
    async def run(
        self,
        *,
        goal: str,
        ctx: ToolContext,
        history: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AgentRunTrace:
        trace = AgentRunTrace()
        async for _ in self.run_stream(
            goal=goal, ctx=ctx, history=history, system_prompt=system_prompt, trace=trace
        ):
            pass
        return trace

    # ── 内部工具 ──────────────────────────────────────────────────────────
    async def _stream_final_answer(
        self, messages: List[Dict[str, Any]], *, task_id: str
    ) -> AsyncIterator[Dict[str, Any]]:
        try:
            async for ev in self.gateway.chat_stream(
                self.config.agent_id,
                messages,
                modality="text",
                task_id=task_id,
                overrides={"max_tokens": self.config.final_max_tokens, "temperature": 0.4},
            ):
                yield ev
        except ModelInvocationError as exc:
            async for ev in self._emit_text_as_stream(
                f"分析已完成，但生成最终回答时模型调用失败（{exc.code}）。"
            ):
                yield ev

    async def _emit_text_as_stream(self, text: str) -> AsyncIterator[Dict[str, Any]]:
        """把已有文本合成为"打字机"流式事件序列（与 chat_stream 事件格式一致）。

        用真实小延时分片下发，让前端逐步渲染；总时长约 _REPLAY_TARGET_CHUNKS×_REPLAY_DELAY，
        与文本长度无关（分片大小自适应），避免长文本回放过久或短文本切太碎。
        """
        message_id = f"msg_{uuid4().hex}"
        yield {"type": "message_start", "message_id": message_id}
        text = text or "我没有生成有效回答，请稍后再试。"
        step = max(_REPLAY_MIN_CHUNK, (len(text) + _REPLAY_TARGET_CHUNKS - 1) // _REPLAY_TARGET_CHUNKS)
        for i in range(0, len(text), step):
            yield {"type": "message_delta", "message_id": message_id, "delta": text[i : i + step]}
            await asyncio.sleep(_REPLAY_DELAY)
        yield {"type": "message_done", "message_id": message_id, "content": text}

    @staticmethod
    def _truncate(text: str) -> str:
        text = text or ""
        if len(text) <= _OBS_MAX_CHARS:
            return text
        return text[:_OBS_MAX_CHARS] + f"\n…（已截断，原始 {len(text)} 字）"

    @staticmethod
    def _short_args(args: Any) -> str:
        try:
            s = json.dumps(args, ensure_ascii=False)
        except Exception:
            s = str(args)
        return s if len(s) <= 120 else s[:120] + "…"


agent_runtime = AgentRuntime()
