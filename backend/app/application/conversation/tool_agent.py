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

from ...domain.conversation import Conversation, IntentClassification
from ...llm.model_gateway import ModelInvocationError, model_gateway
from .intent_router import IntentRouter
from .tool_schema import (
    CONVERSATION_TOOL_DEFINITIONS,
    ConversationToolCall,
    ConversationToolDecision,
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

        # ─── xhs_analysis ───────────────────────────────────────────────────
        if intent_name == "xhs_analysis":
            kw = slots.get("keywords") or intent.extracted_keywords or keywords or []
            comp = slots.get("competitor_keywords") or intent.competitor_keywords or competitor_keywords or []
            skip_competitor = bool(slots.get("skip_competitor", False))
            if not kw:
                kw = IntentRouter.extract_keywords(content, comp)
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="start_xhs_analysis",
                        arguments={
                            "keywords": kw,
                            "competitor_keywords": comp,
                            "skip_competitor": skip_competitor,
                            "confirm_new_task": bool(active_task_id),
                        },
                        confidence=intent.confidence,
                        reason=f"intent=xhs_analysis slots={kw}",
                    )
                ]
            )

        # ─── comment_analysis ────────────────────────────────────────────────
        if intent_name == "comment_analysis":
            kw = slots.get("keywords") or intent.extracted_keywords or keywords or []
            if not kw:
                kw = IntentRouter.extract_keywords(content)
            top_notes = int(slots.get("top_notes") or 0)  # 0 = 不限，爬到多少用多少
            top_comments = int(slots.get("top_comments_per_note") or 5)
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="start_comment_analysis",
                        arguments={
                            "keywords": kw,
                            "top_notes": top_notes,
                            "top_comments_per_note": top_comments,
                        },
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
