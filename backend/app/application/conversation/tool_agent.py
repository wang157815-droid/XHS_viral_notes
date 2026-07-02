"""ConversationToolAgent — 确定性 intent → tool 映射层（第三层）。

职责：接收来自 IntentClassifier 的结构化 IntentClassification（含 slots），
做确定性映射，输出 ConversationToolDecision。不再调用 LLM 做意图二次判断。

唯一保留 LLM 调用的场景：refine_canvas 且 module_id 无法从 slots 中确定时，
调用轻量 LLM 从 canvas_modules 中选出最匹配的模块。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from ...core.config import settings
from ...domain.conversation import Conversation, IntentClassification
from ...llm.model_gateway import ModelInvocationError, model_gateway
from .intent_router import IntentRouter
from .tool_schema import (
    CONVERSATION_TOOL_DEFINITIONS,
    ConversationToolCall,
    ConversationToolDecision,
)

# 反问"workflow vs AI自主"时存入 pending_tool_decision 的占位 tool 名，
# 由 conversation_service._resolve_pending_tool_call 识别并解析用户选择。
ANALYSIS_CHOICE_TOOL = "__analysis_choice__"


class ConversationToolAgent:
    """确定性 intent→tool 映射，不做意图判断。"""

    async def decide(
        self,
        *,
        content: str,
        conversation: Conversation,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
        task_status: Optional[str],
        canvas_modules: List[Dict[str, Any]],
        keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
        advanced_config: Optional[Dict[str, Any]] = None,
    ) -> ConversationToolDecision:
        """根据已分类的 intent 做确定性映射，必要时调用 LLM 解析 module_id。"""
        intent_name = intent.intent
        slots = intent.slots or {}

        agent_enabled = bool(settings.agent_runtime_enabled)
        offer_choice = agent_enabled and bool(settings.agent_runtime_offer_choice)

        # ─── agent_task（自主规划 Agent）────────────────────────────────────
        if intent_name == "agent_task":
            if not agent_enabled:
                # 总开关关闭：降级为普通问答，避免阻塞
                return ConversationToolDecision(
                    calls=[
                        ConversationToolCall(
                            name="answer_general",
                            arguments={"question": content},
                            confidence=max(intent.confidence, 0.6),
                            reason="agent_task but AGENT_RUNTIME_ENABLED=false",
                        )
                    ]
                )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="run_agent_task",
                        arguments={"goal": content},
                        confidence=intent.confidence,
                        reason="intent=agent_task → 自主规划 Agent",
                    )
                ]
            )

        # ─── xhs_analysis ───────────────────────────────────────────────────
        if intent_name == "xhs_analysis":
            kw = slots.get("keywords") or intent.extracted_keywords or keywords or []
            comp = slots.get("competitor_keywords") or intent.competitor_keywords or competitor_keywords or []
            skip_competitor = bool(slots.get("skip_competitor", False))
            if not kw:
                kw = IntentRouter.extract_keywords(content, comp)
            workflow_args = {
                "keywords": kw,
                "competitor_keywords": comp,
                "skip_competitor": skip_competitor,
                "confirm_new_task": bool(active_task_id),
            }
            if offer_choice:
                return self._offer_analysis_choice(
                    workflow_tool="start_xhs_analysis",
                    workflow_arguments=workflow_args,
                    agent_goal=content,
                    keywords=kw,
                    kind="爆文模型",
                    workflow_desc="标准爆文模型矩阵 + Canvas（9 模块）",
                    confidence=intent.confidence,
                )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="start_xhs_analysis",
                        arguments=workflow_args,
                        confidence=intent.confidence,
                        reason=f"intent=xhs_analysis slots={kw}",
                    )
                ]
            )

        # ─── comment_analysis ────────────────────────────────────────────────
        if intent_name == "comment_analysis":
            kw = slots.get("keywords") or intent.extracted_keywords or keywords or []
            if not kw:
                kw = IntentRouter.extract_keywords(content, max_items=5)
            top_notes = int(slots.get("top_notes") or 0)  # 0 = 不限，爬到多少用多少
            top_comments = int(slots.get("top_comments_per_note") or 5)
            workflow_args = {
                "keywords": kw,
                "top_notes": top_notes,
                "top_comments_per_note": top_comments,
            }
            if offer_choice:
                return self._offer_analysis_choice(
                    workflow_tool="start_comment_analysis",
                    workflow_arguments=workflow_args,
                    agent_goal=content,
                    keywords=kw,
                    kind="评论洞察",
                    workflow_desc="标准评论洞察报告（Excel）",
                    confidence=intent.confidence,
                )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="start_comment_analysis",
                        arguments=workflow_args,
                        confidence=intent.confidence,
                        reason=f"intent=comment_analysis slots={kw}",
                    )
                ]
            )

        # ─── refine_canvas ───────────────────────────────────────────────────
        if intent_name == "refine_canvas":
            module_id = slots.get("module_id")
            if not module_id:
                # 从 target_module_ids 中找第一个存在于当前 Canvas 的模块
                for mid in intent.target_module_ids:
                    if any(m.get("module_id") == mid or m.get("moduleId") == mid for m in canvas_modules):
                        module_id = mid
                        break
            if not module_id and canvas_modules:
                # 最后尝试轻量 LLM 帮助选 module_id
                module_id = await self._resolve_module_id(
                    instruction=content,
                    canvas_modules=canvas_modules,
                )
            if not module_id:
                return ConversationToolDecision(
                    calls=[
                        ConversationToolCall(
                            name="ask_clarification",
                            arguments={
                                "question": "你想调整哪个模块？例如：痛点洞察、SEO 关键词、爆文模型矩阵或草稿工作台。",
                                "missing_fields": ["module_id"],
                                "pending_tool_name": "regenerate_canvas_module",
                                "pending_arguments": {"instruction": content, "task_id": active_task_id},
                            },
                            confidence=0.70,
                            reason="refine_canvas 但无法确定目标 module_id",
                        )
                    ]
                )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="regenerate_canvas_module",
                        arguments={
                            "task_id": active_task_id,
                            "module_id": module_id,
                            "instruction": slots.get("instruction") or content,
                            "cascade": False,
                        },
                        confidence=intent.confidence,
                        reason=f"refine_canvas module={module_id}",
                    )
                ]
            )

        # ─── export ──────────────────────────────────────────────────────────
        if intent_name == "export":
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="export_task",
                        arguments={"task_id": active_task_id, "format": "excel"},
                        confidence=intent.confidence,
                        reason="intent=export",
                    )
                ]
            )

        # ─── knowledge_qa ────────────────────────────────────────────────────
        if intent_name == "knowledge_qa":
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="answer_with_knowledge",
                        arguments={"question": content},
                        confidence=intent.confidence,
                        reason="intent=knowledge_qa",
                    )
                ]
            )

        # ─── unknown / clarification needed ──────────────────────────────────
        if intent_name == "unknown" or intent.clarification_needed:
            question = (
                intent.clarification_question
                or "请告诉我你的具体需求，例如：你想搜索哪个产品或品牌的小红书爆文模型？"
            )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="ask_clarification",
                        arguments={
                            "question": question,
                            "missing_fields": intent.missing_fields or [],
                        },
                        confidence=intent.confidence,
                        reason="intent=unknown or clarification_needed",
                    )
                ]
            )

        # ─── general_qa（默认） ───────────────────────────────────────────────
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="answer_general",
                    arguments={"question": content},
                    confidence=max(intent.confidence, 0.65),
                    reason="intent=general_qa",
                )
            ]
        )

    @staticmethod
    def _offer_analysis_choice(
        *,
        workflow_tool: str,
        workflow_arguments: Dict[str, Any],
        agent_goal: str,
        keywords: List[str],
        kind: str,
        workflow_desc: str,
        confidence: float,
    ) -> ConversationToolDecision:
        """命中成熟 workflow 时，反问『定制 workflow vs AI 自主分析』并告知优缺点。

        把两种方案的执行信息存入 pending_tool_decision（占位 tool=__analysis_choice__），
        待用户回复后由 conversation_service._resolve_pending_tool_call 解析为具体工具调用。
        """
        kw_display = "".join(f"「{k}」" for k in keywords) or "该"
        question = (
            f"你对 {kw_display}{kind} 的需求，我可以用两种方式完成，你想用哪种？\n\n"
            f"**① 定制化 workflow（成熟流水线）**\n"
            f"- 优点：流程稳定、产出规范（{workflow_desc}）、速度更快、结果可直接复用\n"
            f"- 局限：只产出标准结构，难以满足超出该范式的定制化要求\n\n"
            f"**② AI 自主分析（Agent 自行规划执行）**\n"
            f"- 优点：按你的具体要求灵活规划，逐条拆解、组合搜索/详情/评论/知识库/联网等能力，适配非标需求\n"
            f"- 局限：耗时更长、产出结构更自由、采集规模受预算上限约束\n\n"
            f"回复「1」或「workflow」走定制流水线；回复「2」或「自主」让 AI 自主分析。"
        )
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="ask_clarification",
                    arguments={
                        "question": question,
                        "missing_fields": ["analysis_mode"],
                        "pending_tool_name": ANALYSIS_CHOICE_TOOL,
                        "pending_arguments": {
                            "workflow_tool": workflow_tool,
                            "workflow_arguments": workflow_arguments,
                            "agent_goal": agent_goal,
                            "kind": kind,
                        },
                    },
                    confidence=confidence,
                    reason=f"offer workflow-vs-agent choice for {kind}",
                )
            ]
        )

    @staticmethod
    async def _resolve_module_id(
        *,
        instruction: str,
        canvas_modules: List[Dict[str, Any]],
    ) -> Optional[str]:
        """用轻量 LLM 从 canvas_modules 选出最匹配 instruction 的 module_id。"""
        module_list = [
            {"module_id": m.get("module_id") or m.get("moduleId"), "title": m.get("title") or m.get("module_id")}
            for m in canvas_modules
            if m.get("module_id") or m.get("moduleId")
        ]
        if not module_list:
            return None
        prompt = (
            f"用户希望调整的内容：{instruction}\n"
            f"当前 Canvas 模块列表：{json.dumps(module_list, ensure_ascii=False)}\n"
            f"请直接返回最匹配的 module_id 字符串（如 mod-pain-points），不要任何解释。"
        )
        try:
            result = await model_gateway.chat(
                "ConversationTitleAgent",
                [{"role": "user", "content": prompt}],
                modality="text",
                overrides={"max_tokens": 50, "temperature": 0.0},
            )
            raw = str(result.get("content") or "").strip()
            matched = re.search(r"mod-[\w-]+", raw)
            if matched:
                mid = matched.group()
                if any(m.get("module_id") == mid or m.get("moduleId") == mid for m in canvas_modules):
                    return mid
        except (ModelInvocationError, Exception) as exc:
            logger.warning("[tool_agent] _resolve_module_id failed: {}", exc)
        return None


conversation_tool_agent = ConversationToolAgent()
