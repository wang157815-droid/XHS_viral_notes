"""
Storage 基础设施（阶段 4.α 引入）。

- `db_engine`：全局 SQLAlchemy async engine(仅用于 pgvector 场景,业务表等 4.6 迁)
- `notes_vector_store`：L2 缓存,小红书笔记向量 + 关键词命中检索

业务表（tasks/identities/knowledge_documents/...）4.6 才统一迁 Postgres,
阶段 4.α **不使用此 engine 访问业务数据**,仅用于笔记向量。
"""

from .db_engine import (
    PostgresSettings,
    check_postgres_health,
    close_db_engine,
    get_db_engine,
    get_db_session,
    is_postgres_available,
)
from .notes_vector_store import NotesVectorStore, get_notes_vector_store

__all__ = [
    "PostgresSettings",
    "check_postgres_health",
    "close_db_engine",
    "get_db_engine",
    "get_db_session",
    "is_postgres_available",
    "NotesVectorStore",
    "get_notes_vector_store",
]
