"""Conversation OS 应用服务。

统一承接 `/conversations/{id}/messages`：保存用户消息、识别意图、执行对应分支、
保存 assistant 消息。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Dict, List, Optional

from loguru import logger

from ..core.config import settings
from ..domain.conversation import ChatMessage, IntentClassification, TaskHandoff, new_id
from ..domain.error_codes import ErrorCode
from ..domain.events import TaskEventType
from ..domain.module_status import ModuleStatus
from ..infrastructure.event_bus import task_event_bus
from ..llm.model_gateway import ModelInvocationError, model_gateway
from ..services.cookie_health_service import cookie_health_service
from ..services.conversation_store import ConversationStore, get_conversation_store
from .module_regeneration import run_module_regeneration
from .orchestration import get_orchestration_engine
from .task_service import task_service
from .conversation.intent_router import IntentRouter, intent_router
from .conversation.knowledge_qa_service import KnowledgeQAService
from .conversation.tool_agent import ConversationToolAgent, conversation_tool_agent
from .conversation.tool_executor import (
    ConversationToolExecutionContext,
    ConversationToolExecutor,
)
from .conversation.tool_schema import ConversationToolCall


class ConversationService:
    def __init__(
        self,
        *,
        store: Optional[ConversationStore] = None,
        router: Optional[IntentRouter] = None,
        knowledge_qa: Optional[KnowledgeQAService] = None,
        tool_agent: Optional[ConversationToolAgent] = None,
        tool_executor: Optional[ConversationToolExecutor] = None,
    ) -> None:
        self.store = store or get_conversation_store()
        self.router = router or intent_router
        self.knowledge_qa = knowledge_qa or KnowledgeQAService()
        self.tool_agent = tool_agent or conversation_tool_agent
        self.tool_executor = tool_executor or ConversationToolExecutor(
            task_service_dep=task_service,
            task_event_bus_dep=task_event_bus,
            get_engine_dep=get_orchestration_engine,
            cookie_health_service_dep=cookie_health_service,
            run_module_regeneration_dep=run_module_regeneration,
        )

    async def handle_user_message(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        content: str,
        keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
        advanced_config: Optional[Dict[str, Any]] = None,
        active_task_id: Optional[str] = None,
        client_message_id: Optional[str] = None,
        current_user: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        conversation = self.store.ensure_owner(conversation_id, owner_user_id)
        if active_task_id:
            conversation = self.store.update_conversation(
                conversation_id,
                active_task_id=active_task_id,
            )

        user_message = ChatMessage(
            message_id=client_message_id or new_id("msg"),
            conversation_id=conversation_id,
            role="user",
            content=content.strip(),
        )
        self.store.append_message(conversation_id, user_message)

        recent_messages = [message.to_dict() for message in self.store.list_messages(conversation_id, limit=10)]
        active_task = active_task_id or conversation.active_task_id
        intent = self.router.classify(
            content=content,
            conversation_summary=conversation.summary,
            recent_messages=recent_messages,
            active_task_id=active_task,
            hint_keywords=keywords,
            competitor_keywords=competitor_keywords,
        )

        assistant_message = await self._dispatch(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            user_message_id=user_message.message_id,
            content=content,
            intent=intent,
            recent_messages=recent_messages,
            active_task_id=active_task,
            current_user=current_user or {"user_id": owner_user_id, "role": "analyst"},
            advanced_config=advanced_config or {},
        )
        self.store.append_message(conversation_id, assistant_message)
        self._maybe_update_summary(conversation_id)

        refreshed = self.store.get(conversation_id) or conversation
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "conversation": refreshed,
            "intent": intent,
        }

    async def handle_user_message_stream(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        content: str,
        keywords: Optional[List[str]] = None,
        competitor_keywords: Optional[List[str]] = None,
        advanced_config: Optional[Dict[str, Any]] = None,
        active_task_id: Optional[str] = None,
        client_message_id: Optional[str] = None,
        current_user: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        conversation = self.store.ensure_owner(conversation_id, owner_user_id)
        if active_task_id:
            conversation = self.store.update_conversation(
                conversation_id,
                active_task_id=active_task_id,
            )

        user_message = ChatMessage(
            message_id=client_message_id or new_id("msg"),
            conversation_id=conversation_id,
            role="user",
            content=content.strip(),
        )
        self.store.append_message(conversation_id, user_message)
        yield {"type": "user_message", "user_message": user_message.to_dict()}
        yield {"type": "status", "status": "thinking", "message": "正在理解你的需求..."}

        recent_messages = [message.to_dict() for message in self.store.list_messages(conversation_id, limit=10)]
        active_task = active_task_id or conversation.active_task_id
        intent = self.router.classify(
            content=content,
            conversation_summary=conversation.summary,
            recent_messages=recent_messages,
            active_task_id=active_task,
            hint_keywords=keywords,
            competitor_keywords=competitor_keywords,
        )
        conversation = self.store.get(conversation_id) or conversation
        canvas_modules = self._canvas_modules_for_context(active_task)
        decision = await self.tool_agent.decide(
            content=content,
            conversation=conversation,
            intent=intent,
            recent_messages=recent_messages,
            active_task_id=active_task,
            task_status=self._task_status_for_context(active_task),
            canvas_modules=canvas_modules,
            keywords=keywords,
            competitor_keywords=competitor_keywords,
            advanced_config=advanced_config or {},
        )
        call = self._resolve_pending_tool_call(conversation.metadata, content, decision.first_call)
        if not call:
            call = ConversationToolCall(name="answer_general", arguments={"question": content}, confidence=0.6)
        yield {"type": "tool_selected", "tool": call.to_dict()}

        current_user = current_user or {"user_id": owner_user_id, "role": "analyst"}
        if call.name == "answer_general":
            async for event in self._stream_general_answer(conversation_id, intent, recent_messages, active_task):
                yield event
            return
        if call.name == "answer_with_knowledge":
            async for event in self._stream_knowledge_answer(conversation_id, intent, content, call, current_user):
                yield event
            return

        ctx = ConversationToolExecutionContext(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            user_message_id=user_message.message_id,
            content=content,
            intent=intent,
            recent_messages=recent_messages,
            active_task_id=active_task,
            current_user=current_user,
            advanced_config=advanced_config or {},
            store=self.store,
            knowledge_qa=self.knowledge_qa,
        )
        yield {"type": "status", "status": "tool_started", "tool": call.name}
        assistant_message = await self.tool_executor.execute(call, ctx)
        self.store.append_message(conversation_id, assistant_message)
        self._maybe_update_summary(conversation_id)
        refreshed = self.store.get(conversation_id) or conversation
        yield {
            "type": "assistant_message",
            "assistant_message": assistant_message.to_dict(),
            "conversation": refreshed.to_dict(),
            "intent": intent.to_dict(),
        }

    async def _dispatch(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        user_message_id: str,
        content: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
        current_user: Dict[str, Any],
        advanced_config: Dict[str, Any],
    ) -> ChatMessage:
        if intent.clarification_needed:
            return self._assistant(
                conversation_id,
                intent,
                intent.clarification_question or "请补充更多信息后我再继续。",
            )

        conversation = self.store.get(conversation_id)
        if not conversation:
            return self._assistant(conversation_id, intent, "当前会话不存在，请刷新后重试。")

        try:
            canvas_modules = self._canvas_modules_for_context(active_task_id)
            task_status = self._task_status_for_context(active_task_id)
            decision = await self.tool_agent.decide(
                content=content,
                conversation=conversation,
                intent=intent,
                recent_messages=recent_messages,
                active_task_id=active_task_id,
                task_status=task_status,
                canvas_modules=canvas_modules,
                keywords=intent.extracted_keywords,
                competitor_keywords=intent.competitor_keywords,
                advanced_config=advanced_config,
            )
            call = self._resolve_pending_tool_call(conversation.metadata, content, decision.first_call)
            if not call:
                call = ConversationToolCall(name="answer_general", arguments={"question": content}, confidence=0.6)
            ctx = ConversationToolExecutionContext(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                user_message_id=user_message_id,
                content=content,
                intent=intent,
                recent_messages=recent_messages,
                active_task_id=active_task_id,
                current_user=current_user,
                advanced_config=advanced_config,
                store=self.store,
                knowledge_qa=self.knowledge_qa,
            )
            return await self.tool_executor.execute(call, ctx)
        except Exception as exc:
            logger.warning("Conversation tool pipeline failed, fallback to legacy dispatch: {}", exc)
            return await self._legacy_dispatch(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                user_message_id=user_message_id,
                content=content,
                intent=intent,
                recent_messages=recent_messages,
                active_task_id=active_task_id,
                current_user=current_user,
                advanced_config=advanced_config,
            )

    async def _legacy_dispatch(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        user_message_id: str,
        content: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
        current_user: Dict[str, Any],
        advanced_config: Dict[str, Any],
    ) -> ChatMessage:
        if intent.intent == "general_qa":
            return await self._general_qa(conversation_id, intent, recent_messages)

        if intent.intent == "knowledge_qa":
            if not settings.conversation_knowledge_qa_enabled:
                return self._assistant(
                    conversation_id,
                    intent,
                    "当前知识库问答功能未启用，我可以先按普通问题帮你分析。",
                    debug={"disabled_flag": "CONVERSATION_KNOWLEDGE_QA_ENABLED"},
                )
            answer, citations, debug = await self.knowledge_qa.answer(
                question=content,
                intent=intent,
                conversation_summary=self.store.get(conversation_id).summary if self.store.get(conversation_id) else "",
                current_user=current_user,
            )
            message = self._assistant(conversation_id, intent, answer, debug=debug)
            message.citations = citations
            return message

        if intent.intent == "xhs_analysis":
            return await self._start_xhs_analysis_task(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                user_message_id=user_message_id,
                content=content,
                intent=intent,
                current_user=current_user,
                advanced_config=advanced_config,
            )

        if intent.intent == "refine_canvas":
            return await self._run_refine_canvas(
                conversation_id=conversation_id,
                content=content,
                intent=intent,
                active_task_id=active_task_id,
            )

        if intent.intent == "export":
            if active_task_id:
                return self._assistant(
                    conversation_id,
                    intent,
                    "可以导出当前分析任务。前端可直接打开现有导出接口下载 Excel 或 JSON。",
                    linked_task_id=active_task_id,
                    debug={
                        "export_urls": {
                            "excel": f"/tasks/{active_task_id}/export/excel",
                            "json": f"/tasks/{active_task_id}/export/json",
                        }
                    },
                )
            return self._assistant(conversation_id, intent, "当前还没有可导出的分析任务，请先生成爆文模型。")

        return self._assistant(conversation_id, intent, "我已经收到，会继续处理你的问题。")

    async def _stream_general_answer(
        self,
        conversation_id: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        messages = self._build_chat_messages(recent_messages)
        content_parts: List[str] = []
        message_id: Optional[str] = None
        async for event in model_gateway.chat_stream(
            "ConversationQAAgent",
            messages,
            modality="text",
            task_id=active_task_id,
            overrides={"max_tokens": 800, "temperature": 0.4},
        ):
            if event.get("type") == "message_start":
                message_id = str(event.get("message_id") or new_id("msg"))
                yield event
                continue
            if event.get("type") == "message_delta":
                content_parts.append(str(event.get("delta") or ""))
                yield event
                continue
            if event.get("type") == "message_error":
                yield event
                return
            if event.get("type") == "message_done":
                content = str(event.get("content") or "".join(content_parts)).strip()
                assistant = ChatMessage(
                    message_id=message_id or str(event.get("message_id") or new_id("msg")),
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content or "我没有生成有效回答，请稍后再试。",
                    intent=intent.intent,
                    intent_confidence=intent.confidence,
                    linked_task_id=active_task_id,
                    debug={"intent_reason": intent.reason, "tool_name": "answer_general", "streamed": True},
                )
                self.store.append_message(conversation_id, assistant)
                self._maybe_update_summary(conversation_id)
                refreshed = self.store.get(conversation_id)
                yield {
                    **event,
                    "message_id": assistant.message_id,
                    "content": assistant.content,
                    "assistant_message": assistant.to_dict(),
                    "conversation": refreshed.to_dict() if refreshed else None,
                    "intent": intent.to_dict(),
                }
                return

    async def _stream_knowledge_answer(
        self,
        conversation_id: str,
        intent: IntentClassification,
        content: str,
        call: ConversationToolCall,
        current_user: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "status", "status": "retrieving_knowledge", "message": "正在检索知识库..."}
        conversation = self.store.get(conversation_id)
        stream_intent = IntentClassification(
            intent="knowledge_qa",
            confidence=max(intent.confidence, call.confidence),
            should_retrieve_knowledge=True,
        )
        try:
            prompt_messages, citations, debug = await self.knowledge_qa.prepare_stream_prompt(
                question=str(call.arguments.get("question") or content),
                intent=stream_intent,
                conversation_summary=conversation.summary if conversation else "",
                current_user=current_user,
            )
        except Exception as exc:
            logger.warning("Knowledge QA stream retrieval unavailable: {}", exc)
            error_message = (
                "当前知识库检索不可用，我可以先按普通经验回答，但不会伪造知识库引用。"
                "请稍后检查 Embedding 或 pgvector 配置后重试。"
            )
            assistant = ChatMessage(
                message_id=new_id("msg"),
                conversation_id=conversation_id,
                role="assistant",
                content=error_message,
                intent="knowledge_qa",
                intent_confidence=stream_intent.confidence,
                debug={"tool_name": "answer_with_knowledge", "model_error_code": "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"},
            )
            self.store.append_message(conversation_id, assistant)
            refreshed = self.store.get(conversation_id)
            yield {
                "type": "message_done",
                "message_id": assistant.message_id,
                "content": assistant.content,
                "assistant_message": assistant.to_dict(),
                "conversation": refreshed.to_dict() if refreshed else None,
                "intent": stream_intent.to_dict(),
            }
            return

        content_parts: List[str] = []
        message_id: Optional[str] = None
        async for event in model_gateway.chat_stream(
            "KnowledgeQAAgent",
            prompt_messages,
            modality="text",
            overrides={"max_tokens": 900, "temperature": 0.2},
        ):
            if event.get("type") == "message_start":
                message_id = str(event.get("message_id") or new_id("msg"))
                yield event
                continue
            if event.get("type") == "message_delta":
                content_parts.append(str(event.get("delta") or ""))
                yield event
                continue
            if event.get("type") == "message_error":
                yield event
                return
            if event.get("type") == "message_done":
                answer = str(event.get("content") or "".join(content_parts)).strip()
                assistant = ChatMessage(
                    message_id=message_id or str(event.get("message_id") or new_id("msg")),
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer or "我没有生成有效回答，请稍后再试。",
                    intent="knowledge_qa",
                    intent_confidence=stream_intent.confidence,
                    citations=citations,
                    debug={"tool_name": "answer_with_knowledge", "streamed": True, **debug},
                )
                self.store.append_message(conversation_id, assistant)
                self._maybe_update_summary(conversation_id)
                refreshed = self.store.get(conversation_id)
                yield {
                    **event,
                    "message_id": assistant.message_id,
                    "content": assistant.content,
                    "assistant_message": assistant.to_dict(),
                    "conversation": refreshed.to_dict() if refreshed else None,
                    "intent": stream_intent.to_dict(),
                }
                return

    async def _start_xhs_analysis_task(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        user_message_id: str,
        content: str,
        intent: IntentClassification,
        current_user: Dict[str, Any],
        advanced_config: Dict[str, Any],
    ) -> ChatMessage:
        try:
            cookie_health = cookie_health_service.get_cookie_health(current_user=current_user, force_check=False)
        except Exception as exc:
            logger.warning("Conversation task handoff cookie health failed: {}", exc)
            return self._assistant(
                conversation_id,
                intent,
                f"创建爆文分析任务前需要检查小红书登录态，但当前检查失败：{exc}",
                debug={"model_error_code": ErrorCode.SYSTEM_INTERNAL.value},
            )

        if cookie_health.get("status") == "expired":
            return self._assistant(
                conversation_id,
                intent,
                f"小红书 Cookie 已过期：{cookie_health.get('message', '请重新登录')}。请重新登录后再发起分析任务。",
                debug={"cookie_health": cookie_health},
            )

        task_advanced_config = {
            **(advanced_config or {}),
            "source": "conversation",
            "conversation_id": conversation_id,
        }
        result = task_service.create_task(
            owner_user_id=owner_user_id,
            raw_input=content,
            keywords=intent.extracted_keywords,
            competitor_keywords=intent.competitor_keywords,
            advanced_config=task_advanced_config,
            idempotency_key=f"conversation:{conversation_id}:message:{user_message_id}:xhs_analysis",
        )

        if result.created:
            await task_event_bus.publish_event(
                task_id=result.record.task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": result.record.status.value, "progress": result.record.progress},
            )
            try:
                await get_orchestration_engine().start(result.record.task_id)
            except Exception as exc:
                logger.warning("Conversation task handoff orchestrator start failed: {}", exc)
                await task_event_bus.publish_event(
                    task_id=result.record.task_id,
                    type=TaskEventType.ERROR,
                    payload={
                        "code": ErrorCode.SYSTEM_INTERNAL.value,
                        "message": f"Orchestrator 启动失败: {exc}",
                    },
                )

        self.store.update_conversation(conversation_id, active_task_id=result.record.task_id)
        handoff = TaskHandoff(
            task_id=result.record.task_id,
            status=result.record.status.value,
            raw_input=content,
            keywords=intent.extracted_keywords,
            canvas_url_hint=f"/workspace?task={result.record.task_id}",
        )
        message = self._assistant(
            conversation_id,
            intent,
            "已创建小红书爆文分析任务，右侧 Canvas 会随着多 Agent 流程逐步生成。",
            linked_task_id=result.record.task_id,
            debug={"idempotent_hit": not result.created, "cookie_health": cookie_health},
        )
        message.task_handoff = handoff
        return message

    async def _run_refine_canvas(
        self,
        *,
        conversation_id: str,
        content: str,
        intent: IntentClassification,
        active_task_id: Optional[str],
    ) -> ChatMessage:
        if not active_task_id:
            return self._assistant(
                conversation_id,
                intent,
                "我能帮你调整 Canvas，但当前会话还没有活跃的分析任务。请先生成一个爆文模型。",
                debug={"requires_active_task": True},
            )

        try:
            canvas = task_service.get_canvas(active_task_id)
        except Exception as exc:
            logger.warning("Conversation refine_canvas get canvas failed: {}", exc)
            return self._assistant(
                conversation_id,
                intent,
                "我找不到当前任务的 Canvas，暂时无法做局部重生。请重新打开任务或先生成爆文模型。",
                linked_task_id=active_task_id,
                debug={"requires_active_task": True, "error": str(exc)},
            )

        module_id = self._resolve_refine_module_id(canvas, intent.target_module_ids)
        if not module_id:
            return self._assistant(
                conversation_id,
                intent,
                "我已识别为 Canvas 调整指令，但还无法确定要改哪个模块。你可以说得更具体一些，例如“优化痛点洞察”“重写 SEO 关键词”“调整爆文模型矩阵”。",
                linked_task_id=active_task_id,
                debug={"requires_module_target": True, "target_module_ids": intent.target_module_ids},
            )

        module = canvas.find_module(module_id)
        if not module:
            return self._assistant(
                conversation_id,
                intent,
                f"我定位到模块 `{module_id}`，但当前 Canvas 中没有这个模块。请换一个模块名称再试。",
                linked_task_id=active_task_id,
                debug={"requires_module_target": True, "target_module_ids": intent.target_module_ids},
            )

        if module.status == ModuleStatus.GENERATING:
            return self._assistant(
                conversation_id,
                intent,
                f"模块「{module.title}」正在生成中，我先不重复触发。等它完成后你可以继续追加修改。",
                linked_task_id=active_task_id,
                debug={"module_id": module_id, "status": module.status.value},
            )

        module.status = ModuleStatus.GENERATING
        module.version += 1
        module.dirty_reason = None
        task_service.set_canvas(active_task_id, canvas)
        await task_event_bus.publish_event(
            task_id=active_task_id,
            type=TaskEventType.CANVAS_MODULE_UPDATED,
            payload={
                "module_id": module_id,
                "status": module.status.value,
                "version": module.version,
                "instruction": content,
                "source": "conversation_refine",
            },
        )
        asyncio.create_task(
            run_module_regeneration(
                task_id=active_task_id,
                module_id=module_id,
                paragraph_id=None,
                instruction=content,
                feedback_hint="conversation_refine",
                cascade=False,
            )
        )
        return self._assistant(
            conversation_id,
            intent,
            f"已按你的追加指令局部重生「{module.title}」，右侧 Canvas 会更新该模块；不会重新跑完整采集分析流程。",
            linked_task_id=active_task_id,
            debug={
                "module_id": module_id,
                "module_title": module.title,
                "regeneration_started": True,
                "target_module_ids": intent.target_module_ids,
            },
        )

    @staticmethod
    def _resolve_refine_module_id(canvas: Any, candidates: List[str]) -> Optional[str]:
        for module_id in candidates:
            if canvas.find_module(module_id):
                return module_id

        fallback_order = [
            "mod-viral-model-matrix",
            "mod-pain-points",
            "mod-seo-insights",
            "mod-draft-workbench",
        ]
        for module_id in fallback_order:
            if module_id in candidates and canvas.find_module(module_id):
                return module_id
        return None

    @staticmethod
    def _canvas_modules_for_context(task_id: Optional[str]) -> List[Dict[str, Any]]:
        if not task_id:
            return []
        try:
            canvas = task_service.get_canvas(task_id)
        except Exception:
            return []
        modules = []
        for module in getattr(canvas, "modules", []) or []:
            modules.append(
                {
                    "module_id": getattr(module, "module_id", ""),
                    "title": getattr(module, "title", ""),
                    "status": getattr(getattr(module, "status", None), "value", getattr(module, "status", "")),
                    "version": getattr(module, "version", 0),
                    "summary": getattr(module, "summary", ""),
                }
            )
        return modules

    @staticmethod
    def _task_status_for_context(task_id: Optional[str]) -> Optional[str]:
        if not task_id:
            return None
        try:
            record = task_service.get_task(task_id)
            return getattr(record.status, "value", str(record.status))
        except Exception:
            return None

    def _resolve_pending_tool_call(
        self,
        metadata: Dict[str, Any],
        content: str,
        selected: Optional[ConversationToolCall],
    ) -> Optional[ConversationToolCall]:
        pending = metadata.get("pending_tool_decision") if isinstance(metadata, dict) else None
        if not isinstance(pending, dict):
            return selected
        pending_tool = str(pending.get("tool_name") or "")
        pending_args = pending.get("arguments") if isinstance(pending.get("arguments"), dict) else {}
        missing_fields = {
            str(item)
            for item in (pending.get("missing_fields") or [])
            if str(item).strip()
        }
        advanced_only_fields = {
            "target_count",
            "sample_count",
            "note_type",
            "time_range",
            "viral_ratio",
            "min_sample_count",
        }
        if selected and selected.name != "answer_general":
            return selected
        if pending_tool == "regenerate_canvas_module":
            target_modules = self.router.extract_target_modules(content)
            if target_modules:
                return ConversationToolCall(
                    name="regenerate_canvas_module",
                    arguments={**pending_args, "module_id": target_modules[0], "instruction": pending_args.get("instruction") or content},
                    confidence=0.74,
                    reason="filled pending regenerate_canvas_module",
                )
        if pending_tool == "start_xhs_analysis":
            if missing_fields and missing_fields.issubset(advanced_only_fields):
                keywords = pending_args.get("keywords")
                if isinstance(keywords, list) and keywords:
                    return ConversationToolCall(
                        name="start_xhs_analysis",
                        arguments={**pending_args, "confirm_new_task": True},
                        confidence=0.76,
                        reason="ignored pending advanced_config-only fields",
                    )
            keywords = self.router.extract_keywords(content)
            if keywords:
                return ConversationToolCall(
                    name="start_xhs_analysis",
                    arguments={**pending_args, "keywords": keywords, "confirm_new_task": True},
                    confidence=0.74,
                    reason="filled pending start_xhs_analysis",
                )
        return selected

    async def _general_qa(
        self,
        conversation_id: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
    ) -> ChatMessage:
        messages = self._build_chat_messages(recent_messages)
        try:
            result = await model_gateway.chat(
                "ConversationQAAgent",
                messages,
                modality="text",
                overrides={"max_tokens": 800, "temperature": 0.4},
            )
            content = str(result.get("content") or "").strip() or "我没有生成有效回答，请稍后再试。"
            return self._assistant(
                conversation_id,
                intent,
                content,
                debug={"model_profile": result.get("profile_id")},
            )
        except ModelInvocationError as exc:
            logger.warning("Conversation QA model unavailable: {} {}", exc.code, exc)
            return self._assistant(
                conversation_id,
                intent,
                f"我已收到你的问题，但当前大模型服务暂时不可用（{exc.code}）。请检查模型配置后重试。",
                debug={"model_error_code": exc.code},
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Conversation QA unexpected error: {}", exc)
            return self._assistant(
                conversation_id,
                intent,
                "我已收到你的问题，但回答生成时发生异常，请稍后重试。",
                debug={"model_error_code": "SYSTEM_INTERNAL"},
            )

    @staticmethod
    def _build_chat_messages(recent_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        system = {
            "role": "system",
            "content": (
                "你是 RedMuse 的工作台助手。用简体中文回答，保持简洁、可执行。"
                "所有自然语言回答必须是合法 Markdown：不要输出 HTML；"
                "需要分点时使用 `- ` 无序列表，需要步骤时使用 `1. ` 编号列表；"
                "代码、命令、路径和字段名使用反引号，较长代码使用 fenced code block；"
                "标题只使用 `##` 或 `###`，不要滥用表格。"
                "如果用户要求小红书采集、爆文模型、知识库引用或 Canvas 修改，不要假装已经执行。"
            ),
        }
        chat_messages: List[Dict[str, Any]] = [system]
        for message in recent_messages[-8:]:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content") or "").strip()
            if content:
                chat_messages.append({"role": role, "content": content})
        return chat_messages

    @staticmethod
    def _assistant(
        conversation_id: str,
        intent: IntentClassification,
        content: str,
        *,
        linked_task_id: Optional[str] = None,
        debug: Optional[Dict[str, Any]] = None,
    ) -> ChatMessage:
        return ChatMessage(
            message_id=new_id("msg"),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            intent=intent.intent,
            intent_confidence=intent.confidence,
            clarification_needed=intent.clarification_needed,
            clarification_question=intent.clarification_question,
            linked_task_id=linked_task_id,
            debug={
                "intent_reason": intent.reason,
                **(debug or {}),
            },
        )

    def _maybe_update_summary(self, conversation_id: str) -> None:
        result = self.store.get_with_messages(conversation_id)
        if not result:
            return
        conversation, messages = result
        if len(messages) <= 10:
            return
        user_messages = [message.content for message in messages if message.role == "user"]
        summary = "；".join(user_messages[-5:])[:500]
        if summary and summary != conversation.summary:
            self.store.update_conversation(conversation_id, summary=summary)


conversation_service = ConversationService()
