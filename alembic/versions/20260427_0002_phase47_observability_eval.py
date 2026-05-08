"""phase 4.7 observability and eval tables

Revision ID: 20260427_0002
Revises: 20260427_0001
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op

from backend.app.infrastructure.db.schema import observability_schema_sql

revision = "20260427_0002"
down_revision = "20260427_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for statement in _split_sql(observability_schema_sql()):
        conn.exec_driver_sql(statement)


def downgrade() -> None:
    conn = op.get_bind()
    for statement in _split_sql(
        """
        DROP TABLE IF EXISTS eval_runs;
        DROP TABLE IF EXISTS task_metrics;
        """
    ):
        conn.exec_driver_sql(statement)


def _split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]
