"""Conversation OS 领域对象。

4.4 首版保持为轻量 dataclass，避免在 API、store 和前端契约之间出现多套字段定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4


ChatRole = Literal["user", "assistant", "system", "tool"]
ConversationIntent = Literal[
    "general_qa",
    "knowledge_qa",
    "xhs_analysis",
    "refine_canvas",
    "export",
    "unknown",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class KnowledgeCitation:
    doc_id: str
    chunk_index: int
    title: str
    snippet: str
    score: float
    source: Literal["vector", "domain_keyword", "task_context"] = "vector"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeCitation":
        return cls(
            doc_id=str(data.get("doc_id") or ""),
            chunk_index=int(data.get("chunk_index") or 0),
            title=str(data.get("title") or ""),
            snippet=str(data.get("snippet") or ""),
            score=float(data.get("score") or 0),
            source=data.get("source") or "vector",
        )


@dataclass
class TaskHandoff:
    task_id: str
    status: str
    raw_input: str
    keywords: List[str] = field(default_factory=list)
    canvas_url_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "raw_input": self.raw_input,
            "keywords": self.keywords,
            "canvas_url_hint": self.canvas_url_hint,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["TaskHandoff"]:
        if not data:
            return None
        return cls(
            task_id=str(data.get("task_id") or ""),
            status=str(data.get("status") or "pending"),
            raw_input=str(data.get("raw_input") or ""),
            keywords=[str(item) for item in data.get("keywords") or []],
            canvas_url_hint=data.get("canvas_url_hint"),
        )


@dataclass
class IntentClassification:
    intent: ConversationIntent = "unknown"
    confidence: float = 0.0
    reason: str = ""
    target_module_ids: List[str] = field(default_factory=list)
    extracted_keywords: List[str] = field(default_factory=list)
    competitor_keywords: List[str] = field(default_factory=list)
    domain_ids: List[str] = field(default_factory=list)
    should_retrieve_knowledge: bool = False
    clarification_needed: bool = False
    clarification_question: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "target_module_ids": self.target_module_ids,
            "extracted_keywords": self.extracted_keywords,
            "competitor_keywords": self.competitor_keywords,
            "domain_ids": self.domain_ids,
            "should_retrieve_knowledge": self.should_retrieve_knowledge,
            "clarification_needed": self.clarification_needed,
            "clarification_question": self.clarification_question,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "IntentClassification":
        data = data or {}
        return cls(
            intent=data.get("intent") or "unknown",
            confidence=float(data.get("confidence") or 0),
            reason=str(data.get("reason") or ""),
            target_module_ids=[str(item) for item in data.get("target_module_ids") or []],
            extracted_keywords=[str(item) for item in data.get("extracted_keywords") or []],
            competitor_keywords=[str(item) for item in data.get("competitor_keywords") or []],
            domain_ids=[str(item) for item in data.get("domain_ids") or []],
            should_retrieve_knowledge=bool(data.get("should_retrieve_knowledge")),
            clarification_needed=bool(data.get("clarification_needed")),
            clarification_question=data.get("clarification_question"),
        )


@dataclass
class ChatMessage:
    message_id: str
    conversation_id: str
    role: ChatRole
    content: str
    intent: ConversationIntent = "unknown"
    intent_confidence: float = 0.0
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    citations: List[KnowledgeCitation] = field(default_factory=list)
    task_handoff: Optional[TaskHandoff] = None
    linked_task_id: Optional[str] = None
    debug: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "intent": self.intent,
            "intent_confidence": self.intent_confidence,
            "clarification_needed": self.clarification_needed,
            "clarification_question": self.clarification_question,
            "citations": [item.to_dict() for item in self.citations],
            "task_handoff": self.task_handoff.to_dict() if self.task_handoff else None,
            "linked_task_id": self.linked_task_id,
            "debug": self.debug,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            message_id=str(data.get("message_id") or new_id("msg")),
            conversation_id=str(data.get("conversation_id") or ""),
            role=data.get("role") or "assistant",
            content=str(data.get("content") or ""),
            intent=data.get("intent") or "unknown",
            intent_confidence=float(data.get("intent_confidence") or 0),
            clarification_needed=bool(data.get("clarification_needed")),
            clarification_question=data.get("clarification_question"),
            citations=[KnowledgeCitation.from_dict(item) for item in data.get("citations") or []],
            task_handoff=TaskHandoff.from_dict(data.get("task_handoff")),
            linked_task_id=data.get("linked_task_id"),
            debug=data.get("debug"),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class Conversation:
    conversation_id: str
    owner_user_id: str
    title: str
    summary: str = ""
    active_task_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "owner_user_id": self.owner_user_id,
            "title": self.title,
            "summary": self.summary,
            "active_task_id": self.active_task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Conversation":
        now = utc_now_iso()
        return cls(
            conversation_id=str(data.get("conversation_id") or new_id("conv")),
            owner_user_id=str(data.get("owner_user_id") or ""),
            title=str(data.get("title") or "新对话"),
            summary=str(data.get("summary") or ""),
            active_task_id=data.get("active_task_id"),
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
            metadata=dict(data.get("metadata") or {}),
        )
