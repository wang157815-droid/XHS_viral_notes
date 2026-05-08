"""Business table DDL helpers for phase 4.6.

Alembic is the authoritative migration path. ``ensure_business_schema`` is used
as a startup/readiness guard and for local smoke runs against fresh databases.
"""

from __future__ import annotations

import os
from typing import Iterable

from loguru import logger

from .engine import get_business_db_engine

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


def embedding_dim() -> int:
    return int(os.getenv("KNOWLEDGE_VECTOR_DIM", os.getenv("NOTES_VECTOR_DIM", "1024")))


def business_schema_sql() -> str:
    dim = embedding_dim()
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tasks (
    task_id VARCHAR(64) PRIMARY KEY,
    owner_user_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    input_spec JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    idempotency_key VARCHAR(256),
    context_version INTEGER NOT NULL DEFAULT 0,
    canvas_version INTEGER NOT NULL DEFAULT 0,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    progress INTEGER NOT NULL DEFAULT 0,
    collected_count INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_code VARCHAR(128),
    last_error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_idempotency_key_uq
    ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_owner_updated_idx ON tasks(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);

CREATE TABLE IF NOT EXISTS identities (
    user_id VARCHAR(128) PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    nickname VARCHAR(255) NOT NULL DEFAULT '',
    role VARCHAR(32) NOT NULL DEFAULT 'user',
    source VARCHAR(64) NOT NULL DEFAULT 'xhs_selfinfo',
    linked_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS identities_updated_idx ON identities(updated_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_domains (
    domain_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    priority VARCHAR(32) NOT NULL DEFAULT 'medium',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    rule_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS knowledge_domains_updated_idx ON knowledge_domains(updated_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    doc_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(512) NOT NULL,
    format VARCHAR(32) NOT NULL DEFAULT 'txt',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    stored_path TEXT,
    vector_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    vector_message TEXT NOT NULL DEFAULT '',
    uploaded_by VARCHAR(128) NOT NULL DEFAULT '',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS knowledge_documents_uploaded_idx ON knowledge_documents(uploaded_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id VARCHAR(128) PRIMARY KEY,
    doc_id VARCHAR(64) NOT NULL REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding_model VARCHAR(128) NOT NULL DEFAULT '',
    embedding vector({dim}),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(doc_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_doc_idx ON knowledge_chunks(doc_id, chunk_index);
CREATE INDEX IF NOT EXISTS knowledge_chunks_domains_gin ON knowledge_chunks USING gin(domains);
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS system_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1,
    settings JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS focus_keywords (
    id SMALLINT PRIMARY KEY DEFAULT 1,
    items JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id VARCHAR(64) PRIMARY KEY,
    owner_user_id VARCHAR(128) NOT NULL,
    title VARCHAR(512) NOT NULL DEFAULT '新对话',
    summary TEXT NOT NULL DEFAULT '',
    active_task_id VARCHAR(64),
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS conversations_owner_updated_idx
    ON conversations(owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role VARCHAR(32) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    intent VARCHAR(64) NOT NULL DEFAULT 'unknown',
    intent_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    clarification_needed BOOLEAN NOT NULL DEFAULT FALSE,
    clarification_question TEXT,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    task_handoff JSONB,
    linked_task_id VARCHAR(64),
    debug JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS conversation_messages_conv_created_idx
    ON conversation_messages(conversation_id, created_at ASC);

{observability_schema_sql()}
"""


def observability_schema_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS task_metrics (
    metric_id VARCHAR(64) PRIMARY KEY,
    task_id VARCHAR(64),
    trace_id VARCHAR(64) NOT NULL DEFAULT '',
    agent VARCHAR(128) NOT NULL DEFAULT '',
    stage VARCHAR(128) NOT NULL DEFAULT '',
    metric_type VARCHAR(64) NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ok',
    error_code VARCHAR(128),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS task_metrics_task_created_idx
    ON task_metrics(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS task_metrics_trace_idx
    ON task_metrics(trace_id);
CREATE INDEX IF NOT EXISTS task_metrics_type_created_idx
    ON task_metrics(metric_type, created_at DESC);
CREATE INDEX IF NOT EXISTS task_metrics_status_created_idx
    ON task_metrics(status, created_at DESC);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    suite VARCHAR(128) NOT NULL,
    mode VARCHAR(32) NOT NULL DEFAULT 'deterministic',
    commit_sha VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'unknown',
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS eval_runs_suite_started_idx
    ON eval_runs(suite, started_at DESC);
CREATE INDEX IF NOT EXISTS eval_runs_mode_started_idx
    ON eval_runs(mode, started_at DESC);
CREATE INDEX IF NOT EXISTS eval_runs_status_started_idx
    ON eval_runs(status, started_at DESC);
"""


def ensure_business_schema() -> None:
    if not _SA_AVAILABLE:
        raise RuntimeError("sqlalchemy 未安装")
    engine = get_business_db_engine()
    with engine.begin() as conn:
        for stmt in _split_sql(business_schema_sql()):
            conn.execute(text(stmt))
    logger.info("[BusinessDB] 业务表 schema 已就绪")


def _split_sql(sql: str) -> Iterable[str]:
    for stmt in sql.split(";"):
        stripped = stmt.strip()
        if stripped:
            yield stripped
