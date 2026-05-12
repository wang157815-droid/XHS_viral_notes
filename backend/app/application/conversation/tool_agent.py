"""LLM backed conversation tool selector."""

from __future__ import annotations

import json
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
    """Choose a bounded backend tool for a user message.

    The LLM never executes side effects. It only returns a tool name and
    arguments; the executor validates everything against backend state.
    """

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
        pending = conversation.metadata.get("pending_tool_decision") or {}
        messages = self._build_messages(
            content=content,
            conversation=conversation,
            intent=intent,
            recent_messages=recent_messages,
            active_task_id=active_task_id,
            task_status=task_status,
            canvas_modules=canvas_modules,
            pending=pending if isinstance(pending, dict) else {},
            keywords=keywords or [],
            competitor_keywords=competitor_keywords or [],
            advanced_config=advanced_config or {},
        )
        try:
            result = await model_gateway.chat_with_tools(
                "ConversationToolAgent",
                messages,
                tools=CONVERSATION_TOOL_DEFINITIONS,
                tool_choice="auto",
                overrides={"temperature": 0.1, "max_tokens": 700},
            )
            calls = self._parse_tool_calls(result)
            if calls:
                return ConversationToolDecision(calls=calls, content=str(result.get("content") or ""), raw=result)
        except ModelInvocationError as exc:
            logger.warning("ConversationToolAgent model unavailable: {} {}", exc.code, exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("ConversationToolAgent unexpected failure: {}", exc)

        return self._fallback_decision(
            content=content,
            intent=intent,
            active_task_id=active_task_id,
            canvas_modules=canvas_modules,
        )

    def _parse_tool_calls(self, result: Dict[str, Any]) -> List[ConversationToolCall]:
        raw_calls = result.get("tool_calls") or []
        calls: List[ConversationToolCall] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            calls.append(
                ConversationToolCall.from_dict(
                    {
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or {},
                        "confidence": item.get("confidence") or 0.82,
                        "reason": item.get("reason") or "model tool call",
                    }
                )
            )
        return calls[:1]

    @staticmethod
    def _build_messages(
        *,
        content: str,
        conversation: Conversation,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
        task_status: Optional[str],
        canvas_modules: List[Dict[str, Any]],
        pending: Dict[str, Any],
        keywords: List[str],
        competitor_keywords: List[str],
        advanced_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        context = {
            "active_task_id": active_task_id,
            "task_status": task_status,
            "conversation_summary": conversation.summary,
            "conversation_metadata": conversation.metadata,
            "pending_tool_decision": pending,
            "rule_intent": intent.to_dict(),
            "hint_keywords": keywords,
            "competitor_keywords": competitor_keywords,
            "advanced_config": advanced_config,
            "canvas_modules": canvas_modules,
            "recent_messages": recent_messages[-6:],
        }
        system = (
            "你是 RedMuse 的 ConversationToolAgent。你的职责是选择一个后端工具，不要直接执行。"
            "如果已有 active_task_id，除非用户明确说重新搜索/新建/重跑/新的分析，否则不得调用 start_xhs_analysis。"
            "创建爆文模型任务时，只要关键词足够就可以调用 start_xhs_analysis。"
            "笔记数量、笔记类型、时间范围、爆款比例已经由上下文 advanced_config 提供，"
            "不得因为缺少 target_count、note_type、time_range、sample_count 或 viral_ratio 向用户追问。"
            "如果用户只是解释、追问原因、要求说明，调用 answer_general。"
            "如果用户要求知识库、文档、SOP、规则、合规或案例，调用 answer_with_knowledge。"
            "如果用户要求调整当前 Canvas 的模块或段落，调用 regenerate_canvas_module；module_id 必须来自 canvas_modules。"
            "只有缺少关键词或模块 ID 等真正无法执行的字段时，才调用 ask_clarification。"
            "如果用户要求导出当前任务，调用 export_task。"
            "多意图输入只选择最主要的一个动作。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"上下文 JSON：{json.dumps(context, ensure_ascii=False)}\n\n用户消息：{content}"},
        ]

    @staticmethod
    def _fallback_decision(
        *,
        content: str,
        intent: IntentClassification,
        active_task_id: Optional[str],
        canvas_modules: List[Dict[str, Any]],
    ) -> ConversationToolDecision:
        if intent.intent == "xhs_analysis":
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="start_xhs_analysis",
                        arguments={
                            "keywords": intent.extracted_keywords or IntentRouter.extract_keywords(content),
                            "competitor_keywords": intent.competitor_keywords,
                            "confirm_new_task": True,
                        },
                        confidence=intent.confidence,
                        reason="rule fallback xhs_analysis",
                    )
                ]
            )
        if intent.intent == "refine_canvas":
            module_id = next(
                (m for m in intent.target_module_ids if any(item.get("module_id") == m for item in canvas_modules)),
                None,
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
                            confidence=0.7,
                            reason="rule fallback needs module target",
                        )
                    ]
                )
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="regenerate_canvas_module",
                        arguments={"task_id": active_task_id, "module_id": module_id, "instruction": content, "cascade": False},
                        confidence=intent.confidence,
                        reason="rule fallback refine_canvas",
                    )
                ]
            )
        if intent.intent == "knowledge_qa":
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="answer_with_knowledge",
                        arguments={"question": content, "top_k": 5},
                        confidence=intent.confidence,
                        reason="rule fallback knowledge_qa",
                    )
                ]
            )
        if intent.intent == "export":
            return ConversationToolDecision(
                calls=[
                    ConversationToolCall(
                        name="export_task",
                        arguments={"task_id": active_task_id, "format": "excel"},
                        confidence=intent.confidence,
                        reason="rule fallback export",
                    )
                ]
            )
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="answer_general",
                    arguments={"question": content},
                    confidence=max(intent.confidence, 0.65),
                    reason="rule fallback general answer",
                )
            ]
        )


conversation_tool_agent = ConversationToolAgent()
