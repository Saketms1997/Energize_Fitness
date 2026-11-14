"""Remember which cloud revision a sync conflict was detected against.

Revision ID: 0003_conflict_remote_rev
Revises: 0002_sync_state
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_conflict_remote_rev"
down_revision: Union[str, None] = "0002_sync_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sync_conflicts", sa.Column("remote_rev", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("sync_conflicts", "remote_rev")
