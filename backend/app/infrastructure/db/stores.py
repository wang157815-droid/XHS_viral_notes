"""Phase 4.6 PostgreSQL store exports.

This module gives tests and future services a single import location for the
runtime SQL-backed stores while the original JSON classes remain available in
their historical modules for explicit unit-test fakes.
"""

from __future__ import annotations

from ...infrastructure.repository.task_repository import SqlAlchemyTaskRepository
from ...services.conversation_store import SqlAlchemyConversationStore
from ...services.focus_keywords_store import SqlAlchemyFocusKeywordsStore
from ...services.identity_store import SqlAlchemyIdentityStore
from ...services.knowledge_registry import SqlAlchemyKnowledgeRegistry
from ...services.system_settings_store import SqlAlchemySystemSettingsStore

__all__ = [
    "SqlAlchemyConversationStore",
    "SqlAlchemyFocusKeywordsStore",
    "SqlAlchemyIdentityStore",
    "SqlAlchemyKnowledgeRegistry",
    "SqlAlchemySystemSettingsStore",
    "SqlAlchemyTaskRepository",
]
