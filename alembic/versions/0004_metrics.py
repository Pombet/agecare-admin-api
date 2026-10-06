"""Create canonical commercial, profile and feature metrics tables.

Revision ID: 0004_metrics
Revises: 0003_staff_audit
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0004_metrics"
down_revision = "0003_staff_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0004_metrics.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
