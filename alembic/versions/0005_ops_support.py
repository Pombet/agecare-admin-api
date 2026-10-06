"""Create canonical operations and support tables.

Revision ID: 0005_ops_support
Revises: 0004_metrics
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0005_ops_support"
down_revision = "0004_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0005_ops_support.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
