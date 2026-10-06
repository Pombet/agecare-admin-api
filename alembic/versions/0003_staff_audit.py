"""Create canonical staff, sessions and audit tables.

Revision ID: 0003_staff_audit
Revises: 0002_core
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0003_staff_audit"
down_revision = "0002_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0003_staff_audit.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
