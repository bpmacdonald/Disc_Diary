"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index(
        "uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True
    )

    op.create_table(
        "artists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mbid", sa.String(36), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("sort_name", sa.String(500), nullable=True),
        sa.Column("type", sa.String(50), nullable=True),
        sa.Column("country", sa.String(10), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artists"),
        sa.UniqueConstraint("mbid", name="uq_artists_mbid"),
    )
    op.create_index(
        "ix_artists_name_trgm",
        "artists",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "release_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mbid", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("primary_type", sa.String(50), nullable=True),
        sa.Column("first_release_date", sa.String(10), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_release_groups"),
        sa.UniqueConstraint("mbid", name="uq_release_groups_mbid"),
    )
    op.create_index(
        "ix_release_groups_title_trgm",
        "release_groups",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    op.create_table(
        "release_group_artists",
        sa.Column("release_group_id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("join_phrase", sa.String(100), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_group_id"],
            ["release_groups.id"],
            name="fk_release_group_artists_release_group_id_release_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name="fk_release_group_artists_artist_id_artists",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "release_group_id", "artist_id", name="pk_release_group_artists"
        ),
    )

    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("release_group_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("review_text", sa.Text(), nullable=True),
        sa.Column("listened_on", sa.Date(), nullable=False),
        sa.Column(
            "is_relisten", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 10", name="rating_range"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_logs_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["release_group_id"],
            ["release_groups.id"],
            name="fk_logs_release_group_id_release_groups",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_logs"),
    )
    op.create_index(
        "ix_logs_user_id_listened_on",
        "logs",
        ["user_id", sa.text("listened_on DESC")],
    )
    op.create_index("ix_logs_release_group_id", "logs", ["release_group_id"])


def downgrade() -> None:
    op.drop_table("logs")
    op.drop_table("release_group_artists")
    op.drop_table("release_groups")
    op.drop_table("artists")
    op.drop_table("users")
    # pg_trgm is left installed: other objects in the DB may depend on it.
