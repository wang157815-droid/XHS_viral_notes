"""Conversation OS 应用服务。

统一承接 `/conversations/{id}/messages`：保存用户消息、识别意图、执行对应分支、
保存 assistant 消息。
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from ..core.config import settings

from ..domain.conversation import ChatMessage, IntentClassification, TaskHandoff, new_id
from ..domain.conversation_surfaces import effective_surface
from ..domain.error_codes import ErrorCode
from ..domain.events import TaskEventType
from ..domain.module_status import ModuleStatus
from ..infrastructure.event_bus import task_event_bus
from ..llm.model_gateway import ModelInvocationError, model_gateway
from ..services.cookie_health_service import cookie_health_service
from ..services.conversation_store import ConversationStore, get_conversation_store
from ..services.web_search_client import fetch_web_context
from .module_regeneration import run_module_regeneration
from .orchestration import get_orchestration_engine
from .task_service import task_service
from .conversation.intent_classifier import IntentClassifier, intent_classifier
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
        classifier: Optional[IntentClassifier] = None,
        knowledge_qa: Optional[KnowledgeQAService] = None,
        tool_agent: Optional[ConversationToolAgent] = None,
        tool_executor: Optional[ConversationToolExecutor] = None,
    ) -> None:
        self.store = store or get_conversation_store()
        self.router = router or intent_router
        self.classifier = classifier or intent_classifier
        self.knowledge_qa = knowledge_qa or KnowledgeQAService()
        self.tool_agent = tool_agent or conversation_tool_agent
        self.tool_executor = tool_executor or ConversationToolExecutor(
            task_service_dep=task_service,
            task_event_bus_dep=task_event_bus,
            get_engine_dep=get_orchestration_engine,
            cookie_health_service_dep=cookie_health_service,
            run_module_regeneration_dep=run_module_regeneration,
        )

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _doc_ids_from_refs(refs: Optional[List[Dict[str, Any]]]) -> Optional[List[str]]:
        if not refs:
            return None
        out = [str(r.get("doc_id")).strip() for r in refs if isinstance(r, dict) and str(r.get("doc_id") or "").strip()]
        return out or None

    @staticmethod
    def _attachment_meta_hints(attachments: Optional[List[Dict[str, Any]]]) -> str:
        """意图分类专用：只返回附件元数据（文件名/类型/大小），不读取内容。"""
        if not attachments:
            return ""
        lines: List[str] = []
        for a in attachments:
            if not isinstance(a, dict):
                continue
            name = str(a.get("filename") or "unknown")
            mime = str(a.get("mime_type") or "unknown")
            size = a.get("size_bytes")
            size_text = f"{size / 1024 / 1024:.1f} MB" if isinstance(size, int) and size > 1024 * 1024 else (
                f"{size / 1024:.1f} KB" if isinstance(size, int) and size > 1024 else f"{size} B"
            ) if isinstance(size, int) else ""
            lines.append(f"- {name}（{mime}{', ' + size_text if size_text else ''}）")
        return "\n".join(lines) if lines else ""

    def _attachment_hints(self, attachments: Optional[List[Dict[str, Any]]], owner_user_id: str) -> str:
        if not attachments:
            return ""
        lines: List[str] = []
        base = self._repo_root() / "datas" / "conversation_uploads"
        for a in attachments:
            if not isinstance(a, dict):
                continue
            sub = str(a.get("storage_subpath") or "").strip().replace("\\", "/")
            if not sub or ".." in sub:
                continue
            if not owner_user_id:
                lines.append(f"- 附件 {a.get('filename') or sub}（未校验归属，已忽略内容）")
                continue
            if not self._is_safe_upload_subpath(owner_user_id, sub):
                lines.append(f"- 附件 {a.get('filename') or sub}（路径校验未通过，已忽略内容）")
                continue
            path = base / sub
            if not path.is_file():
                lines.append(f"- 附件 {a.get('filename') or sub}（服务器上未找到文件）")
                continue
            mime = str(a.get("mime_type") or "").lower()
            name = str(a.get("filename") or path.name)
            name_lower = name.lower()
            # 优先使用上传时预解析的缓存结果
            cache_path = path.parent / f"{path.name}.parsed.json"
            cached_text = ""
            if cache_path.is_file():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
                    cached_text = str(cache.get("text") or "").strip()
                except Exception:
                    pass

            if mime.startswith("text/") or name_lower.endswith((".txt", ".md", ".markdown")):
                try:
                    snippet = cached_text[:4000] if cached_text else path.read_text(encoding="utf-8", errors="replace")[:4000]
                    lines.append(f"- 文本附件 {name} 摘录：\n{snippet}")
                except OSError:
                    lines.append(f"- 文本附件 {name}（读取失败）")
            elif name_lower.endswith(".pdf") or mime == "application/pdf":
                try:
                    text = cached_text
                    if not text:
                        from viral_agent.services.knowledge.document_parser import DocumentParser

                        parsed = DocumentParser().parse_file(str(path))
                        text = str(parsed.get("text") or "").strip()
                    snippet = text[:8000] if text else ""
                    if snippet:
                        lines.append(f"- PDF 附件 {name} 文本摘录：\n{snippet}")
                    else:
                        lines.append(f"- PDF 附件 {name}（未解析出文本，可能为扫描件）")
                except Exception as exc:  # pragma: no cover - optional pypdf
                    logger.warning("conversation PDF attachment parse failed: {}", exc)
                    lines.append(f"- PDF 附件 {name}（解析失败，已保存）")
            elif mime.startswith("image/") or name_lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                lines.append(
                    f"- 图片附件 {name}（{mime or 'image'}，已保存；模型请求中附带图像）"
                )
            else:
                lines.append(f"- 附件 {name}（{mime or 'unknown'}，已保存，分析中仅作引用说明）")
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _is_safe_upload_subpath(owner_user_id: str, sub: str) -> bool:
        sub = sub.replace("\\", "/").strip()
        if not sub or ".." in sub or not owner_user_id:
            return False
        prefix = f"{owner_user_id}/"
        return sub.startswith(prefix) and len(sub) > len(prefix)

    def _vision_parts_from_attachments(
        self,
        owner_user_id: str,
        attachments: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        if not attachments or not owner_user_id:
            return parts
        base = self._repo_root() / "datas" / "conversation_uploads"
        allowed_mimes = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
        for a in attachments:
            if len(parts) >= 4:
                break
            if not isinstance(a, dict):
                continue
            sub = str(a.get("storage_subpath") or "").strip().replace("\\", "/")
            if not self._is_safe_upload_subpath(owner_user_id, sub):
                logger.warning("vision attachment skipped (unsafe path): {}", sub[:80])
                continue
            path = base / sub
            if not path.is_file():
                continue
            mime = (str(a.get("mime_type") or "") or "application/octet-stream").lower()
            name = str(a.get("filename") or path.name).lower()
            if not (mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
                continue
            if mime not in allowed_mimes:
                if mime.startswith("image/"):
                    mime = "image/png"
                elif name.endswith(".png"):
                    mime = "image/png"
                elif name.endswith(".gif"):
                    mime = "image/gif"
                elif name.endswith(".webp"):
                    mime = "image/webp"
                else:
                    mime = "image/jpeg"
            try:
                raw = path.read_bytes()
                b64 = base64.standard_b64encode(raw).decode("ascii")
                url = f"data:{mime};base64,{b64}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            except OSError as exc:
                logger.warning("vision read failed {}: {}", path, exc)
        return parts

    @staticmethod
    def _build_general_qa_messages(
        recent_messages: List[Dict[str, Any]],
        *,
        vision_parts: Optional[List[Dict[str, Any]]] = None,
        doc_hints: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
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
        window = recent_messages[-8:]
        last_user_idx: Optional[int] = None
        for idx in range(len(window) - 1, -1, -1):
            if window[idx].get("role") == "user":
                last_user_idx = idx
                break
        for idx, message in enumerate(window):
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content") or "").strip()
            if role == "user" and idx == last_user_idx:
                text_body = content if content else "（用户上传了附件，请结合附件与对话上文回答。）"
                # 附加文档文本摘录（PDF/TXT/MD 等）
                if doc_hints:
                    text_body = text_body + "\n\n" + doc_hints
                if vision_parts:
                    block: List[Dict[str, Any]] = [{"type": "text", "text": text_body}]
                    block.extend(vision_parts)
                    chat_messages.append({"role": "user", "content": block})
                else:
                    chat_messages.append({"role": "user", "content": text_body})
                continue
            if content:
                chat_messages.append({"role": role, "content": content})
        return chat_messages

    async def _build_classifier_input(
        self,
        *,
        conversation: Any,
        content: str,
        knowledge_refs: Optional[List[Dict[str, Any]]],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> str:
        text = (content or "").strip()
        parts: List[str] = []
        if text:
            parts.append(text)
        elif attachments or knowledge_refs:
            parts.append("（用户未输入文字，请根据其上传的附件与知识库引用理解意图。）")
        else:
            parts.append("")
        ids = self._doc_ids_from_refs(knowledge_refs or [])
        if ids:
            parts.append("[用户指定的知识库文档 doc_id] " + ", ".join(ids))
        # 意图分类只用附件元数据（文件名/类型/大小），不用全文，避免文档内容污染关键词匹配
        att_meta = self._attachment_meta_hints(attachments)
        if att_meta:
            parts.append("[用户上传的附件]\n" + att_meta)
        if effective_surface(conversation.metadata) == "hotspot":
            web = await fetch_web_context(text or "热点")
            if web:
                parts.append("[联网检索参考]\n" + web)
            else:
                parts.append(
                    "[联网检索] 未配置 WEB_SEARCH_API_KEY 或检索无结果；"
                    "以下为模型基于公开常识的推断，非实时网页结论。"
                )
        return "\n\n".join(p for p in parts if p)

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
        attachments: Optional[List[Dict[str, Any]]] = None,
        knowledge_refs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        conversation = self.store.ensure_owner(conversation_id, owner_user_id)
        if active_task_id:
            conversation = self.store.update_conversation(
                conversation_id,
                active_task_id=active_task_id,
            )

        classify_content = await self._build_classifier_input(
            conversation=conversation,
            content=content,
            knowledge_refs=knowledge_refs,
            attachments=attachments,
        )
        dbg: Dict[str, Any] = {}
        if knowledge_refs:
            dbg["knowledge_refs"] = knowledge_refs
        user_message = ChatMessage(
            message_id=client_message_id or new_id("msg"),
            conversation_id=conversation_id,
            role="user",
            content=content.strip(),
            attachments=list(attachments or []),
            debug=dbg or None,
        )
        self.store.append_message(conversation_id, user_message)

        recent_messages = [message.to_dict() for message in self.store.list_messages(conversation_id, limit=10)]
        active_task = active_task_id or conversation.active_task_id
        restrict_docs = self._doc_ids_from_refs(knowledge_refs)
        intent = await self._classify_intent(
            classify_content=classify_content,
            conversation=conversation,
            recent_messages=recent_messages,
            active_task=active_task,
            keywords=keywords,
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
            restrict_knowledge_doc_ids=restrict_docs,
            decision_content=classify_content,
        )
        self.store.append_message(conversation_id, assistant_message)
        self._maybe_update_summary(conversation_id)
        asyncio.create_task(self._auto_title_if_needed(conversation_id))

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
        attachments: Optional[List[Dict[str, Any]]] = None,
        knowledge_refs: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        conversation = self.store.ensure_owner(conversation_id, owner_user_id)
        if active_task_id:
            conversation = self.store.update_conversation(
                conversation_id,
                active_task_id=active_task_id,
            )

        classify_content = await self._build_classifier_input(
            conversation=conversation,
            content=content,
            knowledge_refs=knowledge_refs,
            attachments=attachments,
        )
        dbg: Dict[str, Any] = {}
        if knowledge_refs:
            dbg["knowledge_refs"] = knowledge_refs
        user_message = ChatMessage(
            message_id=client_message_id or new_id("msg"),
            conversation_id=conversation_id,
            role="user",
            content=content.strip(),
            attachments=list(attachments or []),
            debug=dbg or None,
        )
        self.store.append_message(conversation_id, user_message)
        yield {"type": "user_message", "user_message": user_message.to_dict()}
        yield {"type": "status", "status": "thinking", "message": "正在理解你的需求..."}

        recent_messages = [message.to_dict() for message in self.store.list_messages(conversation_id, limit=10)]
        active_task = active_task_id or conversation.active_task_id
        restrict_docs = self._doc_ids_from_refs(knowledge_refs)
        intent = await self._classify_intent(
            classify_content=classify_content,
            conversation=conversation,
            recent_messages=recent_messages,
            active_task=active_task,
            keywords=keywords,
            competitor_keywords=competitor_keywords,
        )
        if intent.clarification_needed:
            q = intent.clarification_question or "请补充更多信息后我再继续。"
            clarification_msg = self._assistant(conversation_id, intent, q)
            self.store.append_message(conversation_id, clarification_msg)
            self._maybe_update_summary(conversation_id)
            asyncio.create_task(self._auto_title_if_needed(conversation_id))
            refreshed = self.store.get(conversation_id) or conversation
            yield {
                "type": "assistant_message",
                "assistant_message": clarification_msg.to_dict(),
                "conversation": refreshed.to_dict(),
                "intent": intent.to_dict(),
            }
            return
        conversation = self.store.get(conversation_id) or conversation
        canvas_modules = self._canvas_modules_for_context(active_task)
        decision = await self.tool_agent.decide(
            content=classify_content,
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
            async for event in self._stream_general_answer(
                conversation_id, owner_user_id, intent, recent_messages, active_task
            ):
                yield event
            return
        if call.name == "answer_with_knowledge":
            async for event in self._stream_knowledge_answer(
                conversation_id, intent, content, call, current_user,
                restrict_knowledge_doc_ids=restrict_docs,
                owner_user_id=owner_user_id,
                attachments=attachments,
            ):
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
            restrict_knowledge_doc_ids=restrict_docs,
        )
        yield {"type": "status", "status": "tool_started", "tool": call.name}
        assistant_message = await self.tool_executor.execute(call, ctx)
        self.store.append_message(conversation_id, assistant_message)
        self._maybe_update_summary(conversation_id)
        asyncio.create_task(self._auto_title_if_needed(conversation_id))
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
        restrict_knowledge_doc_ids: Optional[List[str]] = None,
        decision_content: Optional[str] = None,
    ) -> ChatMessage:
        decide_text = (decision_content or "").strip() or content
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
                content=decide_text,
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
                restrict_knowledge_doc_ids=restrict_knowledge_doc_ids,
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
                restrict_knowledge_doc_ids=restrict_knowledge_doc_ids,
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
        restrict_knowledge_doc_ids: Optional[List[str]] = None,
    ) -> ChatMessage:
        if intent.intent == "general_qa":
            return await self._general_qa(conversation_id, owner_user_id, intent, recent_messages)

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

        # 用户 @了知识库文档但工具管道异常降级到此处时，走知识库问答兜底
        if restrict_knowledge_doc_ids:
            return await self._knowledge_qa_fallback(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                intent=intent,
                content=content,
                current_user=current_user,
                restrict_knowledge_doc_ids=restrict_knowledge_doc_ids,
            )

        return await self._general_qa(conversation_id, owner_user_id, intent, recent_messages)

    async def _knowledge_qa_fallback(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        intent: IntentClassification,
        content: str,
        current_user: Dict[str, Any],
        restrict_knowledge_doc_ids: Optional[List[str]],
    ) -> ChatMessage:
        """工具管道异常降级时的知识库问答兜底（用户已 @文档）。"""
        qa_intent = IntentClassification(
            intent="knowledge_qa",
            confidence=max(intent.confidence, 0.7),
            should_retrieve_knowledge=True,
        )
        try:
            answer, citations, debug = await self.knowledge_qa.answer(
                question=content,
                intent=qa_intent,
                current_user=current_user,
                restrict_doc_ids=restrict_knowledge_doc_ids,
            )
            return ChatMessage(
                message_id=new_id("msg"),
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                intent="knowledge_qa",
                intent_confidence=qa_intent.confidence,
                citations=citations or [],
                debug={**debug, "tool_name": "answer_with_knowledge", "fallback": True},
            )
        except Exception as exc:
            logger.warning("knowledge_qa_fallback failed: {}", exc)
            return await self._general_qa(conversation_id, owner_user_id, intent, [])

    @staticmethod
    async def _stream_think_filter(
        source: AsyncIterator[Dict[str, Any]],
    ) -> AsyncIterator[Dict[str, Any]]:
        """对 chat_stream 产出的事件流进行 think 块过滤。
        - <think> 期间：yield thinking_start（首次）+ thinking_delta（每块）
        - </think> 出现：yield thinking_done（含总耗时）
        - 其他内容：直接 pass-through，且从 content_parts 中排除 think 内容
        """
        import time as _t
        in_think = False
        think_buf: List[str] = []
        think_start: float = 0.0
        message_id: Optional[str] = None

        async for event in source:
            etype = event.get("type")
            if etype == "message_start":
                message_id = str(event.get("message_id") or "")
                yield event
            elif etype == "message_delta":
                raw = str(event.get("delta") or "")
                while raw:
                    if in_think:
                        end = raw.find("</think>")
                        if end >= 0:
                            tail = raw[:end]
                            if tail:
                                think_buf.append(tail)
                                yield {"type": "thinking_delta", "message_id": message_id, "delta": tail}
                            dur_ms = int((_t.monotonic() - think_start) * 1000)
                            yield {
                                "type": "thinking_done",
                                "message_id": message_id,
                                "think": "".join(think_buf),
                                "duration_ms": dur_ms,
                            }
                            think_buf.clear()
                            in_think = False
                            raw = raw[end + len("</think>"):]
                        else:
                            think_buf.append(raw)
                            yield {"type": "thinking_delta", "message_id": message_id, "delta": raw}
                            raw = ""
                    else:
                        start = raw.find("<think>")
                        if start >= 0:
                            visible = raw[:start]
                            if visible:
                                yield {**event, "delta": visible}
                            if not in_think:
                                yield {"type": "thinking_start", "message_id": message_id}
                            in_think = True
                            think_start = _t.monotonic()
                            raw = raw[start + len("<think>"):]
                        else:
                            yield {**event, "delta": raw}
                            raw = ""
            elif etype == "message_done":
                raw_content = str(event.get("content") or "")
                import re as _re
                clean = _re.sub(r"<think>[\s\S]*?</think>", "", raw_content).strip()
                yield {**event, "content": clean}
            else:
                yield event

    async def _stream_general_answer(
        self,
        conversation_id: str,
        owner_user_id: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
        active_task_id: Optional[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        last_user: Optional[Dict[str, Any]] = None
        for m in reversed(recent_messages):
            if m.get("role") == "user":
                last_user = m
                break
        user_text = str((last_user or {}).get("content") or "")
        raw_att = (last_user or {}).get("attachments")
        att_list: List[Dict[str, Any]] = raw_att if isinstance(raw_att, list) else []
        vision_parts = self._vision_parts_from_attachments(owner_user_id, att_list)
        doc_hints = self._attachment_hints(att_list, owner_user_id)
        messages = self._build_general_qa_messages(
            recent_messages, vision_parts=vision_parts or None, doc_hints=doc_hints or None
        )
        modality = "multimodal" if vision_parts else "text"
        content_parts: List[str] = []
        message_id: Optional[str] = None

        async for event in self._stream_think_filter(
            model_gateway.chat_stream(
                "ConversationQAAgent",
                messages,
                modality=modality,
                task_id=active_task_id,
                overrides={"max_tokens": 800, "temperature": 0.4},
            )
        ):
            etype = event.get("type")
            if etype == "message_start":
                message_id = str(event.get("message_id") or new_id("msg"))
                yield event
                continue
            if etype in ("thinking_start", "thinking_delta", "thinking_done"):
                yield event
                continue
            if etype == "message_delta":
                content_parts.append(str(event.get("delta") or ""))
                yield event
                continue
            if etype == "message_error":
                yield event
                return
            if etype == "message_done":
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
                # 先发 message_done 让前端渲染响应内容
                refreshed = self.store.get(conversation_id)
                yield {
                    **event,
                    "message_id": assistant.message_id,
                    "content": assistant.content,
                    "assistant_message": assistant.to_dict(),
                    "conversation": refreshed.to_dict() if refreshed else None,
                    "intent": intent.to_dict(),
                }
                # 流保持，等 LLM 生成标题（超时 15s 直接跳过）
                try:
                    await asyncio.wait_for(
                        self._auto_title_if_needed(conversation_id),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[auto-title] LLM 起名超时，保持原标题")
                updated = self.store.get(conversation_id)
                if updated:
                    yield {"type": "conversation_updated", "conversation": updated.to_dict()}
                return

    async def _stream_knowledge_answer(
        self,
        conversation_id: str,
        intent: IntentClassification,
        content: str,
        call: ConversationToolCall,
        current_user: Dict[str, Any],
        *,
        restrict_knowledge_doc_ids: Optional[List[str]] = None,
        owner_user_id: str = "",
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        yield {"type": "status", "status": "retrieving_knowledge", "message": "正在检索知识库..."}
        conversation = self.store.get(conversation_id)
        stream_intent = IntentClassification(
            intent="knowledge_qa",
            confidence=max(intent.confidence, call.confidence),
            should_retrieve_knowledge=True,
        )
        # 提取图像附件（若有）
        vision_parts = self._vision_parts_from_attachments(owner_user_id, attachments or []) if owner_user_id and attachments else []
        try:
            prompt_messages, citations, debug = await self.knowledge_qa.prepare_stream_prompt(
                question=str(call.arguments.get("question") or content),
                intent=stream_intent,
                conversation_summary=conversation.summary if conversation else "",
                current_user=current_user,
                restrict_doc_ids=restrict_knowledge_doc_ids,
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

        # 若有图像附件，将 vision_parts 注入 prompt_messages 末尾 user 消息
        if vision_parts:
            last_user_idx = None
            for idx in range(len(prompt_messages) - 1, -1, -1):
                if prompt_messages[idx].get("role") == "user":
                    last_user_idx = idx
                    break
            if last_user_idx is not None:
                user_text = str(prompt_messages[last_user_idx].get("content") or "")
                prompt_messages[last_user_idx] = {
                    "role": "user",
                    "content": [{"type": "text", "text": user_text}] + vision_parts,
                }

        modality = "multimodal" if vision_parts else "text"
        content_parts: List[str] = []
        message_id: Optional[str] = None

        async for event in self._stream_think_filter(
            model_gateway.chat_stream(
                "KnowledgeQAAgent",
                prompt_messages,
                modality=modality,
                overrides={"max_tokens": 900, "temperature": 0.2},
            )
        ):
            etype = event.get("type")
            if etype == "message_start":
                message_id = str(event.get("message_id") or new_id("msg"))
                yield event
                continue
            if etype in ("thinking_start", "thinking_delta", "thinking_done"):
                yield event
                continue
            if etype == "message_delta":
                content_parts.append(str(event.get("delta") or ""))
                yield event
                continue
            if etype == "message_error":
                yield event
                return
            if etype == "message_done":
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
                # 先发 message_done 让前端渲染响应内容
                refreshed = self.store.get(conversation_id)
                yield {
                    **event,
                    "message_id": assistant.message_id,
                    "content": assistant.content,
                    "assistant_message": assistant.to_dict(),
                    "conversation": refreshed.to_dict() if refreshed else None,
                    "intent": stream_intent.to_dict(),
                }
                # 等 LLM 生成标题（超时 15s 直接跳过）
                try:
                    await asyncio.wait_for(
                        self._auto_title_if_needed(conversation_id),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[auto-title] LLM 起名超时，保持原标题")
                updated = self.store.get(conversation_id)
                if updated:
                    yield {"type": "conversation_updated", "conversation": updated.to_dict()}
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
            "",
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
        owner_user_id: str,
        intent: IntentClassification,
        recent_messages: List[Dict[str, Any]],
    ) -> ChatMessage:
        last_user: Optional[Dict[str, Any]] = None
        for m in reversed(recent_messages):
            if m.get("role") == "user":
                last_user = m
                break
        raw_att = (last_user or {}).get("attachments")
        att_list: List[Dict[str, Any]] = raw_att if isinstance(raw_att, list) else []
        vision_parts = self._vision_parts_from_attachments(owner_user_id, att_list)
        doc_hints = self._attachment_hints(att_list, owner_user_id)
        messages = self._build_general_qa_messages(
            recent_messages, vision_parts=vision_parts or None, doc_hints=doc_hints or None
        )
        modality = "multimodal" if vision_parts else "text"
        try:
            result = await model_gateway.chat(
                "ConversationQAAgent",
                messages,
                modality=modality,
                overrides={"max_tokens": 800, "temperature": 0.4},
            )
            raw_content = str(result.get("content") or "").strip()
            content = self._strip_think(raw_content) or "我没有生成有效回答，请稍后再试。"
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
    def _strip_think(text: str) -> str:
        """去除文本中的 <think>...</think> 思考块。"""
        import re
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    @staticmethod
    def _build_chat_messages(recent_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return ConversationService._build_general_qa_messages(recent_messages, vision_parts=None)

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

    async def _auto_title_if_needed(self, conversation_id: str) -> None:
        """第一轮回复完成后，调用 LLM 自动生成 ≤10 汉字的会话标题。
        既处理 quick-title 失败后的兜底，也在 auto_titled=True 时尝试 LLM 改善。"""
        import re
        try:
            conversation = self.store.get(conversation_id)
            if not conversation:
                logger.info("[auto-title] ⚠ 会话不存在: {}", conversation_id)
                return

            is_default = conversation.title in ("新对话", "", None)
            was_auto = bool(conversation.metadata.get("auto_titled", False))
            manual_titled = bool(conversation.metadata.get("manual_titled", False))
            logger.info(
                "[auto-title] 开始 conv={} 当前标题=「{}」 is_default={} was_auto={} manual_titled={}",
                conversation_id, conversation.title, is_default, was_auto, manual_titled,
            )
            if manual_titled:
                logger.info("[auto-title] ↩ 用户已手动改名（manual_titled=True），跳过")
                return

            # 只在标题还是默认值时才生成；已经自动起过名后不再重复触发
            if not is_default and was_auto:
                logger.info("[auto-title] ↩ 已自动起名（auto_titled=True），跳过")
                return

            if not is_default and not was_auto:
                logger.info("[auto-title] ↩ 标题已存在且非默认，跳过")
                return

            messages = self.store.list_messages(conversation_id, limit=6)
            logger.info("[auto-title] 消息数={}", len(messages))
            if not messages:
                logger.info("[auto-title] ↩ 无消息，跳过")
                return

            rounds = [
                {"role": m.role, "content": m.content[:300]}
                for m in messages[:6]
            ]
            convo_text = "\n".join(
                f'{"用户" if m["role"] == "user" else "助手"}：{m["content"][:200]}'
                for m in rounds
            )
            logger.info(
                "[auto-title] 发送给 LLM 的对话片段（前100字）: {}",
                convo_text[:100].replace("\n", " | "),
            )
            llm_messages = [
                {
                    "role": "system",
                    "content": (
                        "你是对话标题生成器，禁止思考，直接输出结果。"
                        "根据对话内容生成一个简洁的中文标题，不超过20个汉字，"
                        "禁止出现引号、emoji、句号、逗号，直接输出标题，不要任何解释或标点。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"对话内容：\n{convo_text}\n\n直接输出标题（禁止思考）：",
                },
            ]

            title = ""
            try:
                logger.info("[auto-title] → 调用 LLM（ConversationTitleAgent）...")
                result = await model_gateway.chat(
                    "ConversationTitleAgent",
                    llm_messages,
                    modality="text",
                    overrides={
                        "max_tokens": 100,
                        "temperature": 0.3,
                        "extra_body": {
                            "enable_thinking": False,
                            "thinking": {"type": "disabled"},
                        },
                    },
                )
                raw_full = str(result.get("content") or "")
                raw = self._strip_think(raw_full).strip()
                title = re.sub(r'[""\'\'「」《》。！？，,!?.\s]', "", raw).strip()
                title = title[:20]
                logger.info(
                    "[auto-title] ← 模型返回({}ms) raw='{}' → 清洗后=「{}」",
                    result.get("usage", {}).get("total_tokens", "?"),
                    raw_full[:80].replace("\n", "\\n"), title,
                )
            except Exception as llm_exc:
                logger.warning("[auto-title] ✗ LLM 调用失败: {}", llm_exc)

            if title and title != conversation.title:
                self.store.update_conversation(
                    conversation_id,
                    title=title,
                    metadata_patch={"auto_titled": True},
                )
                logger.info("[auto-title] ✓ 标题已更新: 「{}」→「{}」", conversation.title, title)
            else:
                logger.info(
                    "[auto-title] ↩ 标题未变（title='{}' 与当前='{}' 相同或为空）",
                    title, conversation.title,
                )
        except Exception as exc:
            logger.warning("[auto-title] ✗ 未预期错误: {}", exc)


    async def _classify_intent(
        self,
        *,
        classify_content: str,
        conversation: Any,
        recent_messages: List[Dict[str, Any]],
        active_task: Optional[str],
        keywords: Optional[List[str]],
        competitor_keywords: Optional[List[str]],
    ) -> IntentClassification:
        """两级意图分类：规则快速路径（≥0.90 直接返回）→ LLM 结构化分类（升级路径）。"""
        intent = self.router.classify(
            content=classify_content,
            conversation_summary=conversation.summary,
            recent_messages=recent_messages,
            active_task_id=active_task,
            hint_keywords=keywords,
            competitor_keywords=competitor_keywords,
        )
        logger.info(
            "[intent] 规则层: intent={} confidence={:.2f} reason={}",
            intent.intent, intent.confidence, intent.reason,
        )
        if intent.confidence >= 0.90:
            return intent

        # 置信度不足，升级到 LLM 分类器
        canvas_modules = self._canvas_modules_for_context(active_task)
        task_status = self._task_status_for_context(active_task)
        llm_intent = await self.classifier.classify(
            content=classify_content,
            active_task_id=active_task,
            task_status=task_status,
            recent_messages=recent_messages,
            canvas_modules=canvas_modules,
            hint_keywords=keywords,
            competitor_keywords=competitor_keywords,
        )
        logger.info(
            "[intent] LLM层: intent={} confidence={:.2f} reason={}",
            llm_intent.intent, llm_intent.confidence, llm_intent.reason,
        )
        return llm_intent


conversation_service = ConversationService()
