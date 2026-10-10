"""Local catalog cache: MusicBrainz release groups + Cover Art Archive URLs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Artist, ReleaseGroup, ReleaseGroupArtist
from app.musicbrainz.client import MusicBrainzClient, normalize_mbid
from app.musicbrainz.models import ArtistCredit
from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup

# Served by the static file mount once the UI exists.
PLACEHOLDER_COVER_PATH = "/static/images/cover-placeholder.svg"


def resolve_cover_url(cover_url: str | None) -> str:
    """Prefer the stored Cover Art Archive URL; otherwise the placeholder."""
    return cover_url or PLACEHOLDER_COVER_PATH


def is_fresh(
    fetched_at: datetime,
    *,
    staleness_days: int | None = None,
    now: datetime | None = None,
) -> bool:
    days = settings.cache_staleness_days if staleness_days is None else staleness_days
    current = now or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return fetched_at >= current - timedelta(days=days)


async def fetch_front_cover_url(
    mbid: str, mb_client: MusicBrainzClient
) -> str | None:
    """Ask the Cover Art Archive for a front image URL; None means missing art."""
    return await mb_client.get_front_cover_url(mbid)


async def get_or_fetch_release_group(
    session: Session,
    mbid: str,
    mb_client: MusicBrainzClient,
    *,
    staleness_days: int | None = None,
    now: datetime | None = None,
) -> ReleaseGroup:
    """Return a cached release group, refreshing from MusicBrainz when stale."""
    mbid = normalize_mbid(mbid)
    current = now or datetime.now(timezone.utc)
    cached = _load_release_group(session, mbid)
    if cached is not None and is_fresh(
        cached.fetched_at, staleness_days=staleness_days, now=current
    ):
        return cached

    remote = await mb_client.get_release_group(mbid)
    cover_url = await fetch_front_cover_url(mbid, mb_client)
    return _upsert_release_group(session, remote, cover_url, fetched_at=current)


def _load_release_group(session: Session, mbid: str) -> ReleaseGroup | None:
    return session.scalars(
        select(ReleaseGroup)
        .where(ReleaseGroup.mbid == mbid)
        .options(selectinload(ReleaseGroup.artist_links).selectinload(ReleaseGroupArtist.artist))
    ).first()


def _upsert_release_group(
    session: Session,
    remote: MBReleaseGroup,
    cover_url: str | None,
    *,
    fetched_at: datetime,
) -> ReleaseGroup:
    """Upsert the release group, its artists, and credit links in this transaction."""
    rg_id = session.scalars(
        insert(ReleaseGroup)
        .values(
            mbid=remote.id,
            title=remote.title,
            primary_type=remote.primary_type,
            first_release_date=remote.first_release_date,
            cover_url=cover_url,
            fetched_at=fetched_at,
        )
        .on_conflict_do_update(
            index_elements=[ReleaseGroup.mbid],
            set_={
                "title": remote.title,
                "primary_type": remote.primary_type,
                "first_release_date": remote.first_release_date,
                "cover_url": cover_url,
                "fetched_at": fetched_at,
            },
        )
        .returning(ReleaseGroup.id)
    ).one()

    artist_ids = _upsert_artists(session, remote.artist_credit, fetched_at)
    _replace_artist_links(session, rg_id, remote.artist_credit, artist_ids)
    session.flush()
    # ON CONFLICT updates the row without mutating identity-mapped instances.
    session.expire_all()

    loaded = _load_release_group(session, remote.id)
    if loaded is None:  # pragma: no cover - flush just wrote this row
        raise RuntimeError(f"Release group {remote.id} missing after upsert")
    return loaded


def _upsert_artists(
    session: Session, credits: list[ArtistCredit], fetched_at: datetime
) -> dict[str, int]:
    """Upsert artists from credit refs. Returns mbid -> local id.

    Artist-credit payloads include name/sort-name but not type/country, so an
    existing row keeps those columns on conflict.
    """
    ids: dict[str, int] = {}
    for credit in credits:
        ref = credit.artist
        artist_id = session.scalars(
            insert(Artist)
            .values(
                mbid=ref.id,
                name=ref.name,
                sort_name=ref.sort_name,
                type=None,
                country=None,
                fetched_at=fetched_at,
            )
            .on_conflict_do_update(
                index_elements=[Artist.mbid],
                set_={
                    "name": ref.name,
                    "sort_name": ref.sort_name,
                    "fetched_at": fetched_at,
                },
            )
            .returning(Artist.id)
        ).one()
        ids[ref.id] = artist_id
    return ids


def _replace_artist_links(
    session: Session,
    release_group_id: int,
    credits: list[ArtistCredit],
    artist_ids: dict[str, int],
) -> None:
    # Full replace keeps join_phrase/position in sync with MusicBrainz.
    session.execute(
        delete(ReleaseGroupArtist).where(
            ReleaseGroupArtist.release_group_id == release_group_id
        )
    )
    for position, credit in enumerate(credits):
        session.add(
            ReleaseGroupArtist(
                release_group_id=release_group_id,
                artist_id=artist_ids[credit.artist.id],
                join_phrase=credit.join_phrase or None,
                position=position,
            )
        )
