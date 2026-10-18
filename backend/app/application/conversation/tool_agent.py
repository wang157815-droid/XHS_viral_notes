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

# 旧版反问"workflow vs AI自主"的占位 tool 名；现已统一走自主规划，
# 仅保留以兼容历史会话中残留的 pending_tool_decision。
ANALYSIS_CHOICE_TOOL = "__analysis_choice__"

# 命中即视为"查询/操作系统数据"，交给 Agent 的系统接口工具（tools/system_api.py）
_SYSTEM_QUERY_RE = re.compile(
    r"知识库.{0,8}(哪些|什么|多少|列表|清单|目录)?.{0,4}文档"
    r"|文档(列表|清单)"
    r"|(我的|历史|最近的?|所有)任务"
    r"|任务(列表|详情|进度|状态|画布)"
    r"|(暂停|恢复|取消|重试).{0,10}任务"
    r"|cookie|授权状态|爬虫状态|关注关键词|运营指标|指标汇总|系统设置",
    re.IGNORECASE,
)


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

        # 系统数据查询/操作（知识库文档列表、我的任务、Cookie/爬虫状态等）：
        # 无论 LLM 判成 knowledge_qa / general_qa，都交给 Agent 调用系统接口工具。
        if agent_enabled and intent_name in {"knowledge_qa", "general_qa", "unknown"} and _SYSTEM_QUERY_RE.search(content or ""):
            return self._agent_task_decision(content, max(intent.confidence, 0.8), f"intent={intent_name} 命中系统数据查询 → 自主规划 Agent")

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
            return self._agent_task_decision(content, intent.confidence, "intent=agent_task → 自主规划 Agent")

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
            if agent_enabled:
                # 统一走自主规划：由 Agent 自行决定是否调用流水线阶段工具
                return self._agent_task_decision(content, intent.confidence, "intent=xhs_analysis → 自主规划 Agent")
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
            if agent_enabled:
                # 统一走自主规划：由 Agent 自行决定是否调用流水线阶段工具
                return self._agent_task_decision(content, intent.confidence, "intent=comment_analysis → 自主规划 Agent")
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
    def _agent_task_decision(goal: str, confidence: float, reason: str) -> ConversationToolDecision:
        return ConversationToolDecision(
            calls=[ConversationToolCall(name="run_agent_task", arguments={"goal": goal}, confidence=confidence, reason=reason)]
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
