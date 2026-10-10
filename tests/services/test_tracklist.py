from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReleaseGroup, ReleaseGroupTracklist
from app.musicbrainz.models import (
    Medium,
    RecordingRef,
    ReleaseBrowseResult,
    ReleaseDetail,
    ReleaseSummary,
    Track,
)
from app.services.tracklist import (
    format_duration,
    get_or_fetch_tracklist,
    pick_representative_release,
    tracks_from_release,
)

pytestmark = pytest.mark.anyio

RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
REL_MBID = "0b626195-faad-3b4c-8fc6-efb8950d2770"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def test_pick_representative_prefers_official_earliest_preferred_country() -> None:
    releases = [
        ReleaseSummary(
            id="1", title="A", status="Official", country="JP", date="1997-06-01"
        ),
        ReleaseSummary(
            id="2", title="B", status="Official", country="US", date="1997-05-21"
        ),
        ReleaseSummary(
            id="3", title="C", status="Bootleg", country="US", date="1997-01-01"
        ),
    ]
    chosen = pick_representative_release(releases)
    assert chosen is not None
    assert chosen.id == "2"


def test_tracks_from_release_flattens_media() -> None:
    detail = ReleaseDetail(
        id=REL_MBID,
        title="OK Computer",
        media=[
            Medium(
                position=1,
                tracks=[
                    Track(
                        position=1,
                        number="1",
                        title="Airbag",
                        length=284000,
                        recording=RecordingRef(id="r1", title="Airbag", length=284000),
                    )
                ],
            ),
            Medium(
                position=2,
                tracks=[
                    Track(position=1, number="1", title="Bonus", length=None),
                ],
            ),
        ],
    )
    rows = tracks_from_release(detail)
    assert rows[0]["disc"] == 1 and rows[0]["title"] == "Airbag"
    assert rows[1]["disc"] == 2 and rows[1]["title"] == "Bonus"


def test_format_duration() -> None:
    assert format_duration(284000) == "4:44"
    assert format_duration(None) == ""


def _mb_rg():
    from app.musicbrainz.models import ArtistCredit, ArtistRef
    from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup

    return MBReleaseGroup(
        id=RG_MBID,
        title="OK Computer",
        primary_type="Album",
        first_release_date="1997-05-21",
        artist_credit=[
            ArtistCredit(
                name="Radiohead",
                join_phrase="",
                artist=ArtistRef(
                    id=ARTIST_MBID, name="Radiohead", sort_name="Radiohead"
                ),
            )
        ],
    )


def _client() -> AsyncMock:
    client = AsyncMock()
    client.get_release_group = AsyncMock(return_value=_mb_rg())
    client.get_front_cover_url = AsyncMock(return_value=None)
    client.browse_releases_for_group = AsyncMock(
        return_value=ReleaseBrowseResult(
            **{
                "release-count": 1,
                "release-offset": 0,
                "releases": [
                    {
                        "id": REL_MBID,
                        "title": "OK Computer",
                        "status": "Official",
                        "country": "GB",
                        "date": "1997-05-21",
                        "track-count": 12,
                    }
                ],
            }
        )
    )
    client.get_release = AsyncMock(
        return_value=ReleaseDetail(
            id=REL_MBID,
            title="OK Computer",
            media=[
                Medium(
                    position=1,
                    tracks=[
                        Track(position=1, number="1", title="Airbag", length=284000),
                        Track(position=2, number="2", title="Paranoid Android", length=383000),
                    ],
                )
            ],
        )
    )
    return client


async def test_tracklist_cache_miss_fetches_and_stores(session: Session) -> None:
    client = _client()
    view = await get_or_fetch_tracklist(session, RG_MBID, client, now=NOW)

    assert view.release_mbid == REL_MBID
    assert [t.title for t in view.tracks] == ["Airbag", "Paranoid Android"]
    client.browse_releases_for_group.assert_awaited()
    client.get_release.assert_awaited_once_with(REL_MBID)
    stored = session.scalar(select(ReleaseGroupTracklist))
    assert stored is not None
    assert len(stored.tracks) == 2


async def test_tracklist_cache_hit_skips_api(session: Session) -> None:
    client = _client()
    await get_or_fetch_tracklist(session, RG_MBID, client, now=NOW)
    client.browse_releases_for_group.reset_mock()
    client.get_release.reset_mock()
    client.get_release_group.reset_mock()

    again = await get_or_fetch_tracklist(
        session, RG_MBID, client, now=NOW + timedelta(days=1)
    )

    assert [t.title for t in again.tracks] == ["Airbag", "Paranoid Android"]
    # Release group is still fresh, so no MB calls at all.
    client.get_release_group.assert_not_awaited()
    client.browse_releases_for_group.assert_not_awaited()
    client.get_release.assert_not_awaited()


async def test_stale_tracklist_refreshes(session: Session) -> None:
    client = _client()
    await get_or_fetch_tracklist(session, RG_MBID, client, now=NOW - timedelta(days=40))
    client.get_release.return_value = ReleaseDetail(
        id=REL_MBID,
        title="OK Computer",
        media=[
            Medium(
                position=1,
                tracks=[Track(position=1, number="1", title="Airbag (remaster)", length=1)],
            )
        ],
    )

    view = await get_or_fetch_tracklist(session, RG_MBID, client, now=NOW)

    assert view.tracks[0].title == "Airbag (remaster)"
    assert session.scalar(select(ReleaseGroup)).fetched_at == NOW
