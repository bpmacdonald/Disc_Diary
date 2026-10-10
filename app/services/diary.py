"""Diary and public profile listings for listen logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import groupby
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Log, ReleaseGroup, ReleaseGroupArtist, User
from app.services.catalog import resolve_cover_url
from app.services.logs import rating_to_stars

DIARY_PAGE_SIZE = 25
PROFILE_RECENT_LIMIT = 20


@dataclass(frozen=True)
class DiaryEntry:
    log_id: int
    listened_on: date
    rating: int | None
    stars: str
    is_relisten: bool
    review_text: str | None
    album_mbid: str
    album_title: str
    artists: str
    cover_url: str


@dataclass(frozen=True)
class MonthGroup:
    label: str  # e.g. "October 2026"
    year: int
    month: int
    entries: list[DiaryEntry]


@dataclass(frozen=True)
class Page:
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.total == 0:
            return 1
        return ceil(self.total / self.page_size)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def prev_page(self) -> int | None:
        return self.page - 1 if self.has_prev else None

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.has_next else None


@dataclass(frozen=True)
class DiaryPage:
    groups: list[MonthGroup]
    pagination: Page


@dataclass(frozen=True)
class ProfileStats:
    log_count: int
    album_count: int
    rated_count: int


@dataclass(frozen=True)
class PublicProfile:
    user: User
    stats: ProfileStats
    recent: list[DiaryEntry]


class UserNotFound(Exception):
    """No user with that username."""


def artists_label_for_release_group(rg: ReleaseGroup) -> str:
    parts: list[str] = []
    for link in sorted(rg.artist_links, key=lambda item: item.position):
        parts.append(link.artist.name)
        if link.join_phrase:
            parts.append(link.join_phrase)
    return "".join(parts) if parts else "Unknown artist"


def _entry_from_log(log: Log) -> DiaryEntry:
    rg = log.release_group
    return DiaryEntry(
        log_id=log.id,
        listened_on=log.listened_on,
        rating=log.rating,
        stars=rating_to_stars(log.rating),
        is_relisten=log.is_relisten,
        review_text=log.review_text,
        album_mbid=rg.mbid,
        album_title=rg.title,
        artists=artists_label_for_release_group(rg),
        cover_url=resolve_cover_url(rg.cover_url),
    )


def _log_load_options():
    return (
        selectinload(Log.release_group)
        .selectinload(ReleaseGroup.artist_links)
        .selectinload(ReleaseGroupArtist.artist)
    )


def group_entries_by_month(entries: list[DiaryEntry]) -> list[MonthGroup]:
    """Group a listened_on-desc list into month sections (also newest first)."""
    groups: list[MonthGroup] = []
    for (year, month), items in groupby(
        entries, key=lambda entry: (entry.listened_on.year, entry.listened_on.month)
    ):
        label = date(year, month, 1).strftime("%B %Y")
        groups.append(
            MonthGroup(label=label, year=year, month=month, entries=list(items))
        )
    return groups


def normalize_page(page: int, total: int, page_size: int = DIARY_PAGE_SIZE) -> int:
    if page < 1:
        return 1
    total_pages = max(1, ceil(total / page_size)) if total else 1
    return min(page, total_pages)


def diary_for_user(
    session: Session, user_id: int, *, page: int = 1, page_size: int = DIARY_PAGE_SIZE
) -> DiaryPage:
    total = session.scalar(
        select(func.count()).select_from(Log).where(Log.user_id == user_id)
    )
    assert total is not None
    page = normalize_page(page, total, page_size)
    offset = (page - 1) * page_size
    logs = list(
        session.scalars(
            select(Log)
            .where(Log.user_id == user_id)
            .options(_log_load_options())
            .order_by(Log.listened_on.desc(), Log.created_at.desc(), Log.id.desc())
            .offset(offset)
            .limit(page_size)
        ).unique()
    )
    entries = [_entry_from_log(log) for log in logs]
    return DiaryPage(
        groups=group_entries_by_month(entries),
        pagination=Page(page=page, page_size=page_size, total=total),
    )


def get_user_by_username(session: Session, username: str) -> User:
    user = session.scalar(
        select(User).where(func.lower(User.username) == username.strip().lower())
    )
    if user is None:
        raise UserNotFound(username)
    return user


def profile_stats(session: Session, user_id: int) -> ProfileStats:
    log_count = session.scalar(
        select(func.count()).select_from(Log).where(Log.user_id == user_id)
    )
    album_count = session.scalar(
        select(func.count(func.distinct(Log.release_group_id))).where(
            Log.user_id == user_id
        )
    )
    rated_count = session.scalar(
        select(func.count())
        .select_from(Log)
        .where(Log.user_id == user_id, Log.rating.is_not(None))
    )
    return ProfileStats(
        log_count=int(log_count or 0),
        album_count=int(album_count or 0),
        rated_count=int(rated_count or 0),
    )


def public_profile(
    session: Session, username: str, *, recent_limit: int = PROFILE_RECENT_LIMIT
) -> PublicProfile:
    user = get_user_by_username(session, username)
    logs = list(
        session.scalars(
            select(Log)
            .where(Log.user_id == user.id)
            .options(_log_load_options())
            .order_by(Log.listened_on.desc(), Log.created_at.desc(), Log.id.desc())
            .limit(recent_limit)
        ).unique()
    )
    return PublicProfile(
        user=user,
        stats=profile_stats(session, user.id),
        recent=[_entry_from_log(log) for log in logs],
    )
