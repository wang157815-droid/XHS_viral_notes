"""M1 knowledge_documents.visibility

Revision ID: 20261019_0003
Revises: 20260427_0002
Create Date: 2026-10-19

存量全部回填为 'private'（列默认值），不做任何 shared 推断。
"""

from __future__ import annotations

from alembic import op

from backend.app.infrastructure.db.schema import knowledge_visibility_sql

revision = "20261019_0003"
down_revision = "20260427_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for statement in _split_sql(knowledge_visibility_sql()):
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS visibility")


def _split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]
