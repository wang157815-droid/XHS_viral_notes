"""Conversation tool executor.

Only this layer performs side effects. Tool calls from the model are treated as
untrusted input and are validated against conversation/task/canvas state first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from ...core.config import settings
from ...domain.conversation import ChatMessage, IntentClassification, TaskHandoff, new_id
from ...domain.error_codes import ErrorCode
from ...domain.events import TaskEventType
from ...domain.module_status import ModuleStatus
from ...infrastructure.event_bus import task_event_bus
from ...llm.model_gateway import ModelInvocationError, model_gateway
from ...services.cookie_health_service import cookie_health_service
from ...services.conversation_store import ConversationStore
from ..module_regeneration import run_module_regeneration
from ..orchestration import get_orchestration_engine
from ..task_service import task_service
from .knowledge_qa_service import KnowledgeQAService
from .tool_schema import ConversationToolCall


@dataclass
class ConversationToolExecutionContext:
    conversation_id: str
    owner_user_id: str
    user_message_id: str
    content: str
    intent: IntentClassification
    recent_messages: List[Dict[str, Any]]
    active_task_id: Optional[str]
    current_user: Dict[str, Any]
    advanced_config: Dict[str, Any]
    store: ConversationStore
    knowledge_qa: KnowledgeQAService
    status_events: List[Dict[str, Any]] = field(default_factory=list)
    restrict_knowledge_doc_ids: Optional[List[str]] = None


class ConversationToolExecutor:
    def __init__(
        self,
        *,
        task_service_dep: Any = None,
        task_event_bus_dep: Any = None,
        get_engine_dep: Any = None,
        cookie_health_service_dep: Any = None,
        run_module_regeneration_dep: Any = None,
    ) -> None:
        self.task_service = task_service_dep or task_service
        self.task_event_bus = task_event_bus_dep or task_event_bus
        self.get_orchestration_engine = get_engine_dep or get_orchestration_engine
        self.cookie_health_service = cookie_health_service_dep or cookie_health_service
        self.run_module_regeneration = run_module_regeneration_dep or run_module_regeneration

    async def execute(
        self,
        call: ConversationToolCall,
        ctx: ConversationToolExecutionContext,
    ) -> ChatMessage:
        ctx.status_events.append({"type": "tool_selected", "tool": call.name, "arguments": call.arguments})
        if call.name == "start_xhs_analysis":
            return await self._start_xhs_analysis(call, ctx)
        if call.name == "start_comment_analysis":
            return await self._start_comment_analysis(call, ctx)
        if call.name == "regenerate_canvas_module":
            return await self._regenerate_canvas_module(call, ctx)
        if call.name == "answer_with_knowledge":
            return await self._answer_with_knowledge(call, ctx)
        if call.name == "export_task":
            return self._export_task(call, ctx)
        if call.name == "ask_clarification":
            return self._ask_clarification(call, ctx)
        return await self._answer_general(call, ctx)

    async def _start_xhs_analysis(
        self,
        call: ConversationToolCall,
        ctx: ConversationToolExecutionContext,
    ) -> ChatMessage:
        args = call.arguments
        # 爆文任务主关键词不设硬性上限（仅保留 50 的宽松安全上限）
        keywords = self._clean_list(args.get("keywords"), max_items=50) or ctx.intent.extracted_keywords
        if not keywords:
            return self._ask_clarification(
                ConversationToolCall(
                    name="ask_clarification",
                    arguments={
                        "question": "你想分析哪个品类、品牌或关键词？可以直接说“搜索小红书「防晒」生成爆文模型”。",
                        "missing_fields": ["keywords"],
                        "pending_tool_name": "start_xhs_analysis",
                        "pending_arguments": args,
                    },
                    confidence=call.confidence,
                    reason="missing keywords",
                ),
                ctx,
            )

        try:
            cookie_health = self.cookie_health_service.get_cookie_health(current_user=ctx.current_user, force_check=False)
        except Exception as exc:
            logger.warning("Conversation task handoff cookie health failed: {}", exc)
            return self._assistant(
                ctx,
                f"创建爆文分析任务前需要检查小红书登录态，但当前检查失败：{exc}",
                debug={"tool_name": call.name, "model_error_code": ErrorCode.SYSTEM_INTERNAL.value},
            )

        if cookie_health.get("status") == "expired":
            return self._assistant(
                ctx,
                f"小红书 Cookie 已过期：{cookie_health.get('message', '请重新登录')}。请重新登录后再发起分析任务。",
                debug={"tool_name": call.name, "cookie_health": cookie_health},
            )

        task_advanced_config = {
            **(ctx.advanced_config or {}),
            "source": "conversation",
            "conversation_id": ctx.conversation_id,
        }

        # 将 IntentClassifier 从自然语言中提取的配置槽位合并进 advanced_config
        # 规则：NL 槽位非空时覆盖（用户在对话中明确表达优先），
        #       但不覆盖用户通过 UI 已明确设置的非默认值
        _DEFAULT_CONFIG_VALUES = {"不限", None, ""}
        nl_config_slots = {
            "time_range": ctx.intent.slots.get("time_range"),
            "note_type": ctx.intent.slots.get("note_type"),
            "min_interaction": ctx.intent.slots.get("min_interaction"),
            "sample_count": ctx.intent.slots.get("sample_count"),
        }
        for field_key, nl_value in nl_config_slots.items():
            if not nl_value:
                continue
            ui_value = task_advanced_config.get(field_key)
            if field_key == "sample_count":
                # sample_count 默认值是 "50"，只有 UI 是默认 50 时才用 NL 值覆盖
                if ui_value in _DEFAULT_CONFIG_VALUES or ui_value == "50":
                    task_advanced_config[field_key] = nl_value
            else:
                # 其他字段只有 UI 值是默认（不限/空）时才用 NL 值覆盖
                if ui_value in _DEFAULT_CONFIG_VALUES:
                    task_advanced_config[field_key] = nl_value

        # ── 风险评估 + 竞品确认 ───────────────────────────────────────
        # 把「配置高风险」和「未指定竞品策略」两类需要确认的情况合并为一次对话提问，
        # 避免用户被多次打断。
        risk_acknowledged = bool(args.get("risk_acknowledged"))
        competitor_acknowledged = bool(args.get("competitor_acknowledged"))
        needs_risk = False
        needs_competitor = False
        risk_issues: List[str] = []

        if not risk_acknowledged:
            risk_issues = self._detect_risk_issues(task_advanced_config)
            if risk_issues:
                needs_risk = True

        if not competitor_acknowledged:
            # 用户没有提供竞品词，也没有明确说跳过竞品分析
            has_competitor = bool(
                self._clean_list(args.get("competitor_keywords"))
                or ctx.intent.competitor_keywords
            )
            skip_competitor = bool(
                args.get("skip_competitor")
                or ctx.intent.slots.get("skip_competitor")
            )
            if not has_competitor and not skip_competitor:
                needs_competitor = True

        if needs_risk or needs_competitor:
            pending_args = {
                **args,
                "keywords": keywords,
                "advanced_config_override": task_advanced_config,
                "risk_acknowledged": True,
                "competitor_acknowledged": True,
            }
            missing: List[str] = []
            if needs_risk:
                missing.append("risk_confirmation")
            if needs_competitor:
                missing.append("competitor_confirmation")
            pending = {
                "tool_name": "start_xhs_analysis",
                "arguments": pending_args,
                "missing_fields": missing,
            }
            ctx.store.update_conversation(
                ctx.conversation_id, metadata_patch={"pending_tool_decision": pending}
            )
            return self._assistant(
                ctx,
                "",
                debug={
                    "tool_status": "confirmation_needed",
                    "risk_issues": risk_issues,
                    "risk_keywords": keywords,
                    "needs_risk": needs_risk,
                    "needs_competitor": needs_competitor,
                },
            )

        # 若 pending 里携带了已合并好的 advanced_config_override，直接使用
        if args.get("advanced_config_override") and isinstance(args["advanced_config_override"], dict):
            task_advanced_config = args["advanced_config_override"]

        result = self.task_service.create_task(
            owner_user_id=ctx.owner_user_id,
            raw_input=ctx.content,
            keywords=keywords,
            competitor_keywords=self._clean_list(args.get("competitor_keywords")) or ctx.intent.competitor_keywords,
            advanced_config=task_advanced_config,
            idempotency_key=f"conversation:{ctx.conversation_id}:message:{ctx.user_message_id}:xhs_analysis",
        )

        if result.created:
            await self.task_event_bus.publish_event(
                task_id=result.record.task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": result.record.status.value, "progress": result.record.progress},
            )
            try:
                await self.get_orchestration_engine().start(result.record.task_id)
            except Exception as exc:
                logger.warning("Conversation tool orchestrator start failed: {}", exc)
                await self.task_event_bus.publish_event(
                    task_id=result.record.task_id,
                    type=TaskEventType.ERROR,
                    payload={
                        "code": ErrorCode.SYSTEM_INTERNAL.value,
                        "message": f"Orchestrator 启动失败: {exc}",
                    },
                )

        ctx.store.update_conversation(
            ctx.conversation_id,
            active_task_id=result.record.task_id,
            metadata_patch={"pending_tool_decision": None},
        )
        handoff = TaskHandoff(
            task_id=result.record.task_id,
            status=result.record.status.value,
            raw_input=ctx.content,
            keywords=keywords,
            canvas_url_hint=f"/workspace?task={result.record.task_id}",
            task_type=result.record.task_type,
        )
        message = self._assistant(
            ctx,
            "",
            linked_task_id=result.record.task_id,
            debug={
                "tool_name": call.name,
                "tool_arguments": args,
                "tool_status": "started",
                "confidence": call.confidence,
                "idempotent_hit": not result.created,
                "cookie_health": cookie_health,
            },
        )
        message.task_handoff = handoff
        return message

    async def _start_comment_analysis(
        self,
        call: ConversationToolCall,
        ctx: ConversationToolExecutionContext,
    ) -> ChatMessage:
        """创建评论分析任务并在后台启动 comment_pipeline。"""
        args = call.arguments
        keywords = self._clean_list(args.get("keywords")) or ctx.intent.extracted_keywords
        if not keywords:
            return self._ask_clarification(
                ConversationToolCall(
                    name="ask_clarification",
                    arguments={
                        "question": "你想分析哪个关键词下的评论？请直接说，例如：分析小红书防晒评论区。",
                        "missing_fields": ["keywords"],
                        "pending_tool_name": "start_comment_analysis",
                        "pending_arguments": args,
                    },
                    confidence=call.confidence,
                    reason="missing keywords",
                ),
                ctx,
            )

        top_notes = int(args.get("top_notes") or 0)   # 0 = 不限
        top_comments = int(args.get("top_comments_per_note") or 5)

        result = self.task_service.create_task(
            owner_user_id=ctx.owner_user_id,
            raw_input=ctx.content,
            keywords=keywords,
            advanced_config={
                "source": "conversation",
                "conversation_id": ctx.conversation_id,
                "top_notes": top_notes,
                "top_comments_per_note": top_comments,
            },
            idempotency_key=(
                f"conversation:{ctx.conversation_id}:message:{ctx.user_message_id}:comment_analysis"
            ),
            task_type="comment_analysis",
        )

        if result.created:
            from ..comment_pipeline import run_comment_pipeline
            from ...infrastructure.execution.coordinator import execution_coordinator
            await self.task_event_bus.publish_event(
                task_id=result.record.task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": result.record.status.value, "progress": result.record.progress},
            )
            _comment_task_id = result.record.task_id
            _comment_kwargs = dict(
                task_id=_comment_task_id,
                keywords=keywords,
                raw_input=ctx.content,
                top_notes=top_notes,
                top_comments_per_note=top_comments,
            )

            async def _comment_runner(_handle: Any) -> None:
                await run_comment_pipeline(**_comment_kwargs)

            await execution_coordinator.run_task(_comment_task_id, _comment_runner)

        ctx.store.update_conversation(
            ctx.conversation_id,
            active_task_id=result.record.task_id,
            metadata_patch={"pending_tool_decision": None},
        )
        from ...domain.conversation import TaskHandoff
        handoff = TaskHandoff(
            task_id=result.record.task_id,
            status=result.record.status.value,
            raw_input=ctx.content,
            keywords=keywords,
            canvas_url_hint=None,
            task_type=result.record.task_type,
        )
        from ..comment_pipeline import describe_collection_plan
        kw_display = "".join(f"「{kw}」" for kw in keywords)
        msg = self._assistant(
            ctx,
            (
                f"好的，已为 {kw_display} 启动**评论分析任务**。\n"
                f"{describe_collection_plan(top_notes, top_comments)}。\n"
                f"分析完成后会自动推送下载链接，稍等片刻。"
            ),
            linked_task_id=result.record.task_id,
            debug={
                "tool_name": call.name,
                "tool_status": "started",
                "idempotent_hit": not result.created,
                "keywords": keywords,
                "top_notes": top_notes,
                "top_comments_per_note": top_comments,
            },
        )
        msg.task_handoff = handoff
        return msg

    async def _regenerate_canvas_module(
        self,
        call: ConversationToolCall,
        ctx: ConversationToolExecutionContext,
    ) -> ChatMessage:
        active_task_id = str(call.arguments.get("task_id") or ctx.active_task_id or "")
        if not active_task_id:
            return self._assistant(
                ctx,
                "我能帮你调整 Canvas，但当前会话还没有活跃的分析任务。请先生成一个爆文模型。",
                debug={"tool_name": call.name, "requires_active_task": True},
            )
        try:
            canvas = self.task_service.get_canvas(active_task_id)
        except Exception as exc:
            logger.warning("Conversation tool get canvas failed: {}", exc)
            return self._assistant(
                ctx,
                "我找不到当前任务的 Canvas，暂时无法做局部重生。请重新打开任务或先生成爆文模型。",
                linked_task_id=active_task_id,
                debug={"tool_name": call.name, "requires_active_task": True, "error": str(exc)},
            )
        module_id = str(call.arguments.get("module_id") or "")
        module = canvas.find_module(module_id) if module_id else None
        if not module:
            return self._ask_clarification(
                ConversationToolCall(
                    name="ask_clarification",
                    arguments={
                        "question": "我还无法确定要调整哪个模块。你可以说“优化痛点洞察”“重写 SEO 关键词”或“调整爆文模型矩阵”。",
                        "missing_fields": ["module_id"],
                        "pending_tool_name": "regenerate_canvas_module",
                        "pending_arguments": {**call.arguments, "task_id": active_task_id, "instruction": ctx.content},
                    },
                    confidence=call.confidence,
                    reason="module not found",
                ),
                ctx,
            )
        if module.status == ModuleStatus.GENERATING:
            return self._assistant(
                ctx,
                f"模块「{module.title}」正在生成中，我先不重复触发。等它完成后你可以继续追加修改。",
                linked_task_id=active_task_id,
                debug={"tool_name": call.name, "module_id": module_id, "status": module.status.value},
            )

        instruction = str(call.arguments.get("instruction") or ctx.content)
        module.status = ModuleStatus.GENERATING
        module.version += 1
        module.dirty_reason = None
        self.task_service.set_canvas(active_task_id, canvas)
        await self.task_event_bus.publish_event(
            task_id=active_task_id,
            type=TaskEventType.CANVAS_MODULE_UPDATED,
            payload={
                "module_id": module_id,
                "status": module.status.value,
                "version": module.version,
                "instruction": instruction,
                "source": "conversation_tool",
            },
        )
        asyncio.create_task(
            self.run_module_regeneration(
                task_id=active_task_id,
                module_id=module_id,
                paragraph_id=call.arguments.get("paragraph_id") or None,
                instruction=instruction,
                feedback_hint="conversation_tool",
                cascade=bool(call.arguments.get("cascade")),
            )
        )
        ctx.store.update_conversation(ctx.conversation_id, metadata_patch={"pending_tool_decision": None})
        return self._assistant(
            ctx,
            f"已按你的追加指令局部重生「{module.title}」，右侧 Canvas 会更新该模块；不会重新跑完整采集分析流程。",
            linked_task_id=active_task_id,
            debug={
                "tool_name": call.name,
                "tool_arguments": call.arguments,
                "tool_status": "started",
                "confidence": call.confidence,
                "module_id": module_id,
                "module_title": module.title,
                "regeneration_started": True,
            },
        )

    async def _answer_with_knowledge(self, call: ConversationToolCall, ctx: ConversationToolExecutionContext) -> ChatMessage:
        if not settings.conversation_knowledge_qa_enabled:
            return self._assistant(
                ctx,
                "当前知识库问答功能未启用，我可以先按普通问题帮你分析。",
                debug={"tool_name": call.name, "disabled_flag": "CONVERSATION_KNOWLEDGE_QA_ENABLED"},
            )
        intent = IntentClassification(
            intent="knowledge_qa",
            confidence=max(ctx.intent.confidence, call.confidence),
            should_retrieve_knowledge=True,
        )
        answer, citations, debug = await ctx.knowledge_qa.answer(
            question=str(call.arguments.get("question") or ctx.content),
            intent=intent,
            conversation_summary=ctx.store.get(ctx.conversation_id).summary if ctx.store.get(ctx.conversation_id) else "",
            current_user=ctx.current_user,
            restrict_doc_ids=ctx.restrict_knowledge_doc_ids,
        )
        message = self._assistant(ctx, answer, debug={"tool_name": call.name, **debug})
        message.citations = citations
        return message

    async def _answer_general(self, call: ConversationToolCall, ctx: ConversationToolExecutionContext) -> ChatMessage:
        messages = self._build_chat_messages(ctx.recent_messages)
        try:
            result = await model_gateway.chat(
                "ConversationQAAgent",
                messages,
                modality="text",
                overrides={"max_tokens": 800, "temperature": 0.4},
            )
            content = str(result.get("content") or "").strip() or "我没有生成有效回答，请稍后再试。"
            return self._assistant(ctx, content, debug={"tool_name": call.name, "model_profile": result.get("profile_id")})
        except ModelInvocationError as exc:
            logger.warning("Conversation QA model unavailable: {} {}", exc.code, exc)
            return self._assistant(
                ctx,
                f"我已收到你的问题，但当前大模型服务暂时不可用（{exc.code}）。请检查模型配置后重试。",
                debug={"tool_name": call.name, "model_error_code": exc.code},
            )

    def _export_task(self, call: ConversationToolCall, ctx: ConversationToolExecutionContext) -> ChatMessage:
        task_id = str(call.arguments.get("task_id") or ctx.active_task_id or "")
        if not task_id:
            return self._assistant(ctx, "当前还没有可导出的分析任务，请先生成爆文模型。")
        fmt = str(call.arguments.get("format") or "excel").lower()
        if fmt not in {"excel", "json"}:
            fmt = "excel"
        return self._assistant(
            ctx,
            "可以导出当前分析任务。前端可直接打开现有导出接口下载 Excel 或 JSON。",
            linked_task_id=task_id,
            debug={
                "tool_name": call.name,
                "tool_arguments": call.arguments,
                "export_urls": {
                    "excel": f"/tasks/{task_id}/export/excel",
                    "json": f"/tasks/{task_id}/export/json",
                },
                "selected_format": fmt,
            },
        )

    def _ask_clarification(self, call: ConversationToolCall, ctx: ConversationToolExecutionContext) -> ChatMessage:
        args = call.arguments
        pending = {
            "tool_name": args.get("pending_tool_name") or "answer_general",
            "arguments": args.get("pending_arguments") or {},
            "missing_fields": self._clean_list(args.get("missing_fields")),
        }
        ctx.store.update_conversation(ctx.conversation_id, metadata_patch={"pending_tool_decision": pending})
        question = str(args.get("question") or "请补充更多信息后我再继续。")
        return self._assistant(
            ctx,
            question,
            debug={
                "tool_name": call.name,
                "tool_status": "clarification_required",
                "pending_tool_decision": pending,
            },
        )

    @staticmethod
    def _detect_risk_issues(config: Dict[str, Any]) -> List[str]:
        """检测配置中的高风险项，返回问题描述列表。无风险时返回空列表。"""
        import re as _re

        issues: List[str] = []

        raw_count = config.get("sample_count")
        if raw_count:
            try:
                count = int(str(raw_count).strip())
                if count > 100:
                    issues.append(f"样本量 {count} 条（推荐上限 100）")
            except (ValueError, TypeError):
                pass

        raw_inter = config.get("min_interaction")
        if raw_inter and str(raw_inter) not in ("不限", "", "None"):
            m = _re.search(r"(\d+)", str(raw_inter))
            if m and int(m.group(1)) > 2000:
                issues.append(f"互动量门槛 {raw_inter}（推荐上限 2000）")

        return issues

    async def _ask_risk_confirmation(self, *args, **kwargs):
        """已废弃：风险确认改由 conversation_service._stream_risk_confirmation 流式处理。"""
        raise NotImplementedError("_ask_risk_confirmation is removed; handled by conversation_service")

    @staticmethod
    def _assistant(
        ctx: ConversationToolExecutionContext,
        content: str,
        *,
        linked_task_id: Optional[str] = None,
        debug: Optional[Dict[str, Any]] = None,
    ) -> ChatMessage:
        return ChatMessage(
            message_id=new_id("msg"),
            conversation_id=ctx.conversation_id,
            role="assistant",
            content=content,
            intent=ctx.intent.intent,
            intent_confidence=ctx.intent.confidence,
            clarification_needed=bool((debug or {}).get("tool_status") == "clarification_required"),
            clarification_question=content if (debug or {}).get("tool_status") == "clarification_required" else None,
            linked_task_id=linked_task_id,
            debug={
                "intent_reason": ctx.intent.reason,
                **(debug or {}),
            },
        )

    @staticmethod
    def _clean_list(value: Any, max_items: int = 5) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        result: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result[:max_items]

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


conversation_tool_executor = ConversationToolExecutor()
