"""phase 4.6 business tables

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op

from backend.app.infrastructure.db.schema import business_schema_sql

revision = "20260427_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for statement in _split_sql(business_schema_sql()):
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    for statement in _split_sql(
        """
        DROP TABLE IF EXISTS conversation_messages;
        DROP TABLE IF EXISTS conversations;
        DROP TABLE IF EXISTS focus_keywords;
        DROP TABLE IF EXISTS system_settings;
        DROP TABLE IF EXISTS knowledge_chunks;
        DROP TABLE IF EXISTS knowledge_documents;
        DROP TABLE IF EXISTS knowledge_domains;
        DROP TABLE IF EXISTS identities;
        DROP TABLE IF EXISTS tasks;
        """
    ):
        conn.exec_driver_sql(statement)


def _split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]
