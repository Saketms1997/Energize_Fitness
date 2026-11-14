"""Sync engine state: per-record merge base and local key/value meta.

Revision ID: 0002_sync_state
Revises: 0001_initial_schema
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_sync_state"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "sync_base",
        sa.Column("entity_table", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("remote_rev", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("entity_table", "entity_id", name="pk_sync_base"),
    )
    op.create_table(
        "sync_meta",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_sync_meta"),
    )


def downgrade() -> None:
    op.drop_table("sync_meta")
    op.drop_table("sync_base")
