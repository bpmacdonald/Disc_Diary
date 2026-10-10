from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    logs: Mapped[list[Log]] = relationship(back_populates="user")


class Artist(Base):
    __tablename__ = "artists"
    __table_args__ = (
        Index(
            "ix_artists_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mbid: Mapped[str] = mapped_column(String(36), unique=True)
    name: Mapped[str] = mapped_column(String(500))
    sort_name: Mapped[str | None] = mapped_column(String(500))
    type: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(10))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    release_group_links: Mapped[list[ReleaseGroupArtist]] = relationship(
        back_populates="artist"
    )


class ReleaseGroup(Base):
    __tablename__ = "release_groups"
    __table_args__ = (
        Index(
            "ix_release_groups_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mbid: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    primary_type: Mapped[str | None] = mapped_column(String(50))
    # MusicBrainz dates may be partial ("1997", "1997-05"), so keep as text.
    first_release_date: Mapped[str | None] = mapped_column(String(10))
    cover_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    artist_links: Mapped[list[ReleaseGroupArtist]] = relationship(
        back_populates="release_group", order_by="ReleaseGroupArtist.position"
    )
    logs: Mapped[list[Log]] = relationship(back_populates="release_group")
    tracklist: Mapped[ReleaseGroupTracklist | None] = relationship(
        back_populates="release_group", uselist=False
    )


class ReleaseGroupTracklist(Base):
    """Cached tracklist for one representative release of a release group.

    `tracks` is a JSON list of
    ``{disc, position, number, title, length_ms, recording_mbid}``.
    """

    __tablename__ = "release_group_tracklists"

    release_group_id: Mapped[int] = mapped_column(
        ForeignKey("release_groups.id", ondelete="CASCADE"), primary_key=True
    )
    release_mbid: Mapped[str] = mapped_column(String(36))
    release_title: Mapped[str | None] = mapped_column(String(500))
    tracks: Mapped[list] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    release_group: Mapped[ReleaseGroup] = relationship(back_populates="tracklist")


class ReleaseGroupArtist(Base):
    __tablename__ = "release_group_artists"

    release_group_id: Mapped[int] = mapped_column(
        ForeignKey("release_groups.id", ondelete="CASCADE"), primary_key=True
    )
    artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True
    )
    join_phrase: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)

    release_group: Mapped[ReleaseGroup] = relationship(back_populates="artist_links")
    artist: Mapped[Artist] = relationship(back_populates="release_group_links")


class Log(Base):
    __tablename__ = "logs"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 10", name="rating_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    release_group_id: Mapped[int] = mapped_column(
        ForeignKey("release_groups.id", ondelete="RESTRICT")
    )
    rating: Mapped[int | None] = mapped_column(Integer)
    review_text: Mapped[str | None] = mapped_column(Text)
    listened_on: Mapped[date] = mapped_column(Date)
    is_relisten: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="logs")
    release_group: Mapped[ReleaseGroup] = relationship(back_populates="logs")


# Case-insensitive uniqueness: "Ben" and "ben" collide.
Index("uq_users_username_lower", func.lower(User.username), unique=True)

# Serves "a user's diary, newest first" without a sort step.
Index("ix_logs_user_id_listened_on", Log.user_id, Log.listened_on.desc())

# Serves "all logs for this album" and FK lookups/cascade checks.
Index("ix_logs_release_group_id", Log.release_group_id)
