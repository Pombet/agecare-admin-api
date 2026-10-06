"""Create canonical tenant and shared catalog foundation.

Revision ID: 0002_core
Revises: 0001
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0002_core"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0002_core.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
