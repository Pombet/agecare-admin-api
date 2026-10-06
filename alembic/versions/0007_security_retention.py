"""Apply canonical history, row security, grants, partitions and seed data.

Revision ID: 0007_security_retention
Revises: 0006_business_modules
"""

from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = "0007_security_retention"
down_revision = "0006_business_modules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_snapshot("0007_security_retention.sql")


def downgrade() -> None:
    raise RuntimeError("Canonical schema migrations are irreversible; restore a database backup instead.")
