"""Frozen prototype baseline for databases created before the canonical schema.

Revision ID: 0001
Create Date: 2026-08-28
"""
from alembic import op

from app.database import Base
from app import legacy_models  # noqa: F401 (registers the historical tables)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
