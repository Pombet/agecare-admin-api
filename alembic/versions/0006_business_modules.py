"""Create canonical content, marketplace, moderation, settings and jobs tables.

Revision ID: 0006_business_modules
Revises: 0005_ops_support
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0006_business_modules"
down_revision = "0005_ops_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0006_business_modules.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
