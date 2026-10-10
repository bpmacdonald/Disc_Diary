from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, ReleaseGroup, ReleaseGroupArtist
from app.musicbrainz.models import ArtistCredit, ArtistRef
from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup
from app.services.catalog import (
    PLACEHOLDER_COVER_PATH,
    fetch_front_cover_url,
    get_or_fetch_release_group,
    resolve_cover_url,
)

pytestmark = pytest.mark.anyio

RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
COVER_URL = "https://coverartarchive.org/release-group/b1392450-e666-3926-a536-22c65f834433/front-500"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _mb_release_group(
    *, title: str = "OK Computer", artist_name: str = "Radiohead"
) -> MBReleaseGroup:
    return MBReleaseGroup(
        id=RG_MBID,
        title=title,
        primary_type="Album",
        first_release_date="1997-05-21",
        artist_credit=[
            ArtistCredit(
                name=artist_name,
                join_phrase="",
                artist=ArtistRef(
                    id=ARTIST_MBID, name=artist_name, sort_name=artist_name
                ),
            )
        ],
        tags=[],
    )


def _fake_client(
    remote: MBReleaseGroup | None = None,
    cover_url: str | None = COVER_URL,
) -> Any:
    client = AsyncMock()
    client.get_release_group = AsyncMock(return_value=remote or _mb_release_group())
    client.get_front_cover_url = AsyncMock(return_value=cover_url)
    return client


def _seed_cached(
    session: Session,
    *,
    fetched_at: datetime,
    title: str = "Cached Title",
    cover_url: str | None = "https://example.com/old.jpg",
) -> ReleaseGroup:
    artist = Artist(
        mbid=ARTIST_MBID,
        name="Radiohead",
        sort_name="Radiohead",
        type="Group",
        country="GB",
        fetched_at=fetched_at,
    )
    rg = ReleaseGroup(
        mbid=RG_MBID,
        title=title,
        primary_type="Album",
        first_release_date="1997-05-21",
        cover_url=cover_url,
        fetched_at=fetched_at,
    )
    session.add_all([artist, rg])
    session.flush()
    session.add(
        ReleaseGroupArtist(
            release_group_id=rg.id,
            artist_id=artist.id,
            join_phrase=None,
            position=0,
        )
    )
    session.flush()
    return rg


async def test_cache_hit_makes_no_api_call(session: Session) -> None:
    _seed_cached(session, fetched_at=NOW - timedelta(days=1))
    client = _fake_client()

    result = await get_or_fetch_release_group(
        session, RG_MBID, client, now=NOW, staleness_days=30
    )

    assert result.title == "Cached Title"
    client.get_release_group.assert_not_awaited()
    client.get_front_cover_url.assert_not_awaited()


async def test_cache_miss_fetches_and_stores(session: Session) -> None:
    client = _fake_client(remote=_mb_release_group(title="OK Computer"))

    result = await get_or_fetch_release_group(
        session, RG_MBID, client, now=NOW, staleness_days=30
    )

    client.get_release_group.assert_awaited_once_with(RG_MBID)
    client.get_front_cover_url.assert_awaited_once_with(RG_MBID)
    assert result.title == "OK Computer"
    assert result.cover_url == COVER_URL
    assert result.fetched_at == NOW
    assert len(result.artist_links) == 1
    assert result.artist_links[0].artist.mbid == ARTIST_MBID
    assert result.artist_links[0].artist.name == "Radiohead"

    # Persisted for a later session load.
    stored = session.scalars(
        select(ReleaseGroup).where(ReleaseGroup.mbid == RG_MBID)
    ).one()
    assert stored.cover_url == COVER_URL
    assert session.scalars(select(Artist).where(Artist.mbid == ARTIST_MBID)).one()


async def test_stale_entry_refreshes(session: Session) -> None:
    _seed_cached(
        session,
        fetched_at=NOW - timedelta(days=40),
        title="Stale Title",
        cover_url="https://example.com/old.jpg",
    )
    client = _fake_client(
        remote=_mb_release_group(title="OK Computer", artist_name="Radiohead"),
        cover_url=COVER_URL,
    )

    result = await get_or_fetch_release_group(
        session, RG_MBID, client, now=NOW, staleness_days=30
    )

    client.get_release_group.assert_awaited_once()
    assert result.title == "OK Computer"
    assert result.cover_url == COVER_URL
    assert result.fetched_at == NOW
    # Existing artist type/country are preserved on credit-only upsert.
    artist = session.scalars(select(Artist).where(Artist.mbid == ARTIST_MBID)).one()
    assert artist.type == "Group"
    assert artist.country == "GB"


async def test_missing_cover_art_stores_none_and_placeholder_used(
    session: Session,
) -> None:
    client = _fake_client(cover_url=None)

    result = await get_or_fetch_release_group(
        session, RG_MBID, client, now=NOW, staleness_days=30
    )

    assert result.cover_url is None
    assert resolve_cover_url(result.cover_url) == PLACEHOLDER_COVER_PATH


async def test_fetch_front_cover_url_helper_delegates() -> None:
    client = _fake_client(cover_url=COVER_URL)
    assert await fetch_front_cover_url(RG_MBID, client) == COVER_URL
    client.get_front_cover_url.assert_awaited_once_with(RG_MBID)


def test_resolve_cover_url_prefers_stored() -> None:
    assert resolve_cover_url(COVER_URL) == COVER_URL
    assert resolve_cover_url(None) == PLACEHOLDER_COVER_PATH
