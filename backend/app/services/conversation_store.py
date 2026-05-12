"""Conversation JSON store.

首版与当前任务仓库一样使用本地 JSON，后续 4.6 可整体迁移到 PostgreSQL。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

from ..domain.conversation import ChatMessage, Conversation, new_id, utc_now_iso
from ..infrastructure.db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


class ConversationStore:
    def __init__(self, store_dir: Optional[str] = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self._store_dir = Path(store_dir) if store_dir else repo_root / "datas" / "conversations"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def create(
        self,
        *,
        owner_user_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Conversation:
        now = utc_now_iso()
        conversation = Conversation(
            conversation_id=new_id("conv"),
            owner_user_id=owner_user_id,
            title=(title or "新对话").strip() or "新对话",
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        with self._lock:
            self._write(conversation, [])
        return conversation

    def get(self, conversation_id: str) -> Optional[Conversation]:
        data = self._read(conversation_id)
        if not data:
            return None
        return Conversation.from_dict(data.get("conversation") or {})

    def get_with_messages(self, conversation_id: str) -> Optional[Tuple[Conversation, List[ChatMessage]]]:
        data = self._read(conversation_id)
        if not data:
            return None
        conversation = Conversation.from_dict(data.get("conversation") or {})
        messages = [ChatMessage.from_dict(item) for item in data.get("messages") or []]
        return conversation, messages

    def list_for_owner(
        self,
        owner_user_id: str,
        *,
        limit: int = 50,
        include_all: bool = False,
        include_archived: bool = False,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """返回会话摘要列表，避免历史中心加载每条完整消息正文。"""
        kw = (keyword or "").strip().lower()
        summaries: List[Dict[str, Any]] = []
        with self._lock:
            paths = list(self._store_dir.glob("*.json"))

        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                conversation = Conversation.from_dict(raw.get("conversation") or {})
                if not include_all and conversation.owner_user_id != owner_user_id:
                    continue
                if conversation.metadata.get("deleted") is True and not include_archived:
                    continue
                if not include_archived and conversation.metadata.get("archived") is True:
                    continue

                messages = [ChatMessage.from_dict(item) for item in raw.get("messages") or []]
                summary = self._build_summary(conversation, messages)
                if kw and kw not in self._summary_search_text(summary).lower():
                    continue
                summaries.append(summary)
            except Exception:
                continue

        summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return summaries[:limit]

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> List[ChatMessage]:
        data = self._read(conversation_id)
        if not data:
            return []
        messages = [ChatMessage.from_dict(item) for item in data.get("messages") or []]
        if before:
            for idx, message in enumerate(messages):
                if message.message_id == before:
                    messages = messages[:idx]
                    break
        return messages[-limit:]

    def list_messages_page(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> Tuple[List[ChatMessage], bool]:
        data = self._read(conversation_id)
        if not data:
            return [], False
        messages = [ChatMessage.from_dict(item) for item in data.get("messages") or []]
        if before:
            for idx, message in enumerate(messages):
                if message.message_id == before:
                    messages = messages[:idx]
                    break
        has_more = len(messages) > limit
        return messages[-limit:], has_more

    def append_message(self, conversation_id: str, message: ChatMessage) -> ChatMessage:
        with self._lock:
            data = self._read(conversation_id)
            if not data:
                raise KeyError(f"Conversation not found: {conversation_id}")
            conversation = Conversation.from_dict(data.get("conversation") or {})
            messages = [ChatMessage.from_dict(item) for item in data.get("messages") or []]
            messages.append(message)
            conversation.updated_at = utc_now_iso()
            self._write(conversation, messages)
        return message

    def update_conversation(
        self,
        conversation_id: str,
        *,
        active_task_id: Optional[str] = None,
        summary: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> Conversation:
        with self._lock:
            data = self._read(conversation_id)
            if not data:
                raise KeyError(f"Conversation not found: {conversation_id}")
            conversation = Conversation.from_dict(data.get("conversation") or {})
            messages = [ChatMessage.from_dict(item) for item in data.get("messages") or []]
            if active_task_id is not None:
                conversation.active_task_id = active_task_id
            if summary is not None:
                conversation.summary = summary
            if metadata_patch:
                conversation.metadata.update(metadata_patch)
            conversation.updated_at = utc_now_iso()
            self._write(conversation, messages)
            return conversation

    def ensure_owner(self, conversation_id: str, owner_user_id: str) -> Conversation:
        conversation = self.get(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")
        if conversation.owner_user_id != owner_user_id:
            raise PermissionError("Conversation owner mismatch")
        return conversation

    def _path(self, conversation_id: str) -> Path:
        safe = "".join(ch for ch in conversation_id if ch.isalnum() or ch in {"_", "-"})
        return self._store_dir / f"{safe}.json"

    def _read(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(conversation_id)
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, conversation: Conversation, messages: List[ChatMessage]) -> None:
        payload = {
            "conversation": conversation.to_dict(),
            "messages": [message.to_dict() for message in messages],
        }
        path = self._path(conversation.conversation_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _build_summary(conversation: Conversation, messages: List[ChatMessage]) -> Dict[str, Any]:
        last_message = messages[-1] if messages else None
        last_assistant = next((message for message in reversed(messages) if message.role == "assistant"), None)
        preview_source = last_message.content if last_message else conversation.summary
        citations_count = sum(len(message.citations) for message in messages)
        return {
            "conversation_id": conversation.conversation_id,
            "owner_user_id": conversation.owner_user_id,
            "title": conversation.title,
            "summary": conversation.summary,
            "active_task_id": conversation.active_task_id,
            "last_message_preview": ConversationStore._truncate(preview_source, 120),
            "last_intent": (last_assistant or last_message).intent if (last_assistant or last_message) else "unknown",
            "message_count": len(messages),
            "citations_count": citations_count,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "metadata": conversation.metadata,
        }

    @staticmethod
    def _summary_search_text(summary: Dict[str, Any]) -> str:
        metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
        recent_keywords = metadata.get("recent_keywords") if isinstance(metadata, dict) else []
        parts = [
            summary.get("conversation_id"),
            summary.get("title"),
            summary.get("summary"),
            summary.get("last_message_preview"),
            summary.get("last_intent"),
            summary.get("active_task_id"),
            *(recent_keywords or []),
        ]
        return " ".join(str(part) for part in parts if part)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return f"{compact[:limit].rstrip()}..."


class SqlAlchemyConversationStore(ConversationStore):
    """PostgreSQL-backed conversation store."""

    def __init__(self) -> None:
        self._lock = RLock()

    def create(
        self,
        *,
        owner_user_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Conversation:
        now = utc_now_iso()
        conversation = Conversation(
            conversation_id=new_id("conv"),
            owner_user_id=owner_user_id,
            title=(title or "新对话").strip() or "新对话",
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO conversations(
                        conversation_id, owner_user_id, title, summary, active_task_id,
                        metadata, created_at, updated_at
                    ) VALUES (
                        :conversation_id, :owner_user_id, :title, :summary, :active_task_id,
                        CAST(:metadata AS jsonb), CAST(:created_at AS timestamptz),
                        CAST(:updated_at AS timestamptz)
                    )
                    """
                ),
                self._conversation_params(conversation),
            )
        return conversation

    def get(self, conversation_id: str) -> Optional[Conversation]:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT * FROM conversations WHERE conversation_id = :conversation_id"),
                {"conversation_id": conversation_id},
            ).mappings().first()
        return self._conversation_from_row(row) if row else None

    def get_with_messages(self, conversation_id: str) -> Optional[Tuple[Conversation, List[ChatMessage]]]:
        conversation = self.get(conversation_id)
        if not conversation:
            return None
        return conversation, self.list_messages(conversation_id, limit=1000)

    def list_for_owner(
        self,
        owner_user_id: str,
        *,
        limit: int = 50,
        include_all: bool = False,
        include_archived: bool = False,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where = []
        params: Dict[str, Any] = {"limit": limit}
        if not include_all:
            where.append("c.owner_user_id = :owner_user_id")
            params["owner_user_id"] = owner_user_id
        if not include_archived:
            where.append("COALESCE((c.metadata ->> 'archived')::boolean, false) = false")
            where.append("COALESCE((c.metadata ->> 'deleted')::boolean, false) = false")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with get_business_db_session() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT c.*,
                           COUNT(m.message_id) AS message_count,
                           COALESCE(SUM(jsonb_array_length(m.citations)), 0) AS citations_count
                    FROM conversations c
                    LEFT JOIN conversation_messages m ON m.conversation_id = c.conversation_id
                    {where_sql}
                    GROUP BY c.conversation_id
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        summaries = [self._summary_from_conversation_row(row) for row in rows]
        kw = (keyword or "").strip().lower()
        if kw:
            summaries = [s for s in summaries if kw in self._summary_search_text(s).lower()]
        return summaries

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> List[ChatMessage]:
        messages, _ = self.list_messages_page(conversation_id, limit=limit, before=before)
        return messages

    def list_messages_page(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> Tuple[List[ChatMessage], bool]:
        params: Dict[str, Any] = {"conversation_id": conversation_id, "limit": limit + 1}
        before_clause = ""
        if before:
            before_clause = """
                AND created_at < (
                    SELECT created_at FROM conversation_messages WHERE message_id = :before
                )
            """
            params["before"] = before
        with get_business_db_session() as session:
            rows = session.execute(
                text(
                    f"""
                    SELECT * FROM conversation_messages
                    WHERE conversation_id = :conversation_id
                    {before_clause}
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        has_more = len(rows) > limit
        selected = list(reversed(rows[:limit]))
        return [self._message_from_row(row) for row in selected], has_more

    def append_message(self, conversation_id: str, message: ChatMessage) -> ChatMessage:
        if not self.get(conversation_id):
            raise KeyError(f"Conversation not found: {conversation_id}")
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO conversation_messages(
                        message_id, conversation_id, role, content, intent, intent_confidence,
                        clarification_needed, clarification_question, citations, task_handoff,
                        linked_task_id, debug, created_at
                    ) VALUES (
                        :message_id, :conversation_id, :role, :content, :intent, :intent_confidence,
                        :clarification_needed, :clarification_question, CAST(:citations AS jsonb),
                        CAST(:task_handoff AS jsonb), :linked_task_id, CAST(:debug AS jsonb),
                        CAST(:created_at AS timestamptz)
                    )
                    """
                ),
                self._message_params(message),
            )
            session.execute(
                text("UPDATE conversations SET updated_at = NOW() WHERE conversation_id = :conversation_id"),
                {"conversation_id": conversation_id},
            )
        return message

    def update_conversation(
        self,
        conversation_id: str,
        *,
        active_task_id: Optional[str] = None,
        summary: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> Conversation:
        conversation = self.get(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")
        if active_task_id is not None:
            conversation.active_task_id = active_task_id
        if summary is not None:
            conversation.summary = summary
        if metadata_patch:
            conversation.metadata.update(metadata_patch)
        conversation.updated_at = utc_now_iso()
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    UPDATE conversations SET
                        active_task_id = :active_task_id,
                        summary = :summary,
                        metadata = CAST(:metadata AS jsonb),
                        updated_at = CAST(:updated_at AS timestamptz)
                    WHERE conversation_id = :conversation_id
                    """
                ),
                self._conversation_params(conversation),
            )
        return conversation

    @staticmethod
    def _iso(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value or utc_now_iso())

    @classmethod
    def _conversation_from_row(cls, row: Any) -> Conversation:
        data = dict(row)
        data["created_at"] = cls._iso(data.get("created_at"))
        data["updated_at"] = cls._iso(data.get("updated_at"))
        data["metadata"] = dict(data.get("metadata") or {})
        return Conversation.from_dict(data)

    @classmethod
    def _message_from_row(cls, row: Any) -> ChatMessage:
        data = dict(row)
        data["created_at"] = cls._iso(data.get("created_at"))
        data["citations"] = data.get("citations") or []
        data["debug"] = data.get("debug")
        data["task_handoff"] = data.get("task_handoff")
        return ChatMessage.from_dict(data)

    @staticmethod
    def _conversation_params(conversation: Conversation) -> Dict[str, Any]:
        return {
            "conversation_id": conversation.conversation_id,
            "owner_user_id": conversation.owner_user_id,
            "title": conversation.title,
            "summary": conversation.summary,
            "active_task_id": conversation.active_task_id,
            "metadata": json.dumps(conversation.metadata or {}, ensure_ascii=False),
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }

    @staticmethod
    def _message_params(message: ChatMessage) -> Dict[str, Any]:
        return {
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "role": message.role,
            "content": message.content,
            "intent": message.intent,
            "intent_confidence": message.intent_confidence,
            "clarification_needed": message.clarification_needed,
            "clarification_question": message.clarification_question,
            "citations": json.dumps([item.to_dict() for item in message.citations], ensure_ascii=False),
            "task_handoff": json.dumps(message.task_handoff.to_dict(), ensure_ascii=False)
            if message.task_handoff
            else None,
            "linked_task_id": message.linked_task_id,
            "debug": json.dumps(message.debug, ensure_ascii=False) if message.debug is not None else None,
            "created_at": message.created_at,
        }

    def _summary_from_conversation_row(self, row: Any) -> Dict[str, Any]:
        conversation = self._conversation_from_row(row)
        messages = self.list_messages(conversation.conversation_id, limit=1)
        summary = self._build_summary(conversation, messages)
        summary["message_count"] = int(row.get("message_count") or 0)
        summary["citations_count"] = int(row.get("citations_count") or 0)
        return summary


_default_store = SqlAlchemyConversationStore()


def get_conversation_store() -> ConversationStore:
    return _default_store
