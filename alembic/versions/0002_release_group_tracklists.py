"""release group tracklist cache

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "release_group_tracklists",
        sa.Column("release_group_id", sa.Integer(), nullable=False),
        sa.Column("release_mbid", sa.String(length=36), nullable=False),
        sa.Column("release_title", sa.String(length=500), nullable=True),
        sa.Column("tracks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_group_id"],
            ["release_groups.id"],
            name="fk_release_group_tracklists_release_group_id_release_groups",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("release_group_id", name="pk_release_group_tracklists"),
    )


def downgrade() -> None:
    op.drop_table("release_group_tracklists")
