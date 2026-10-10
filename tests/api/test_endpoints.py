from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from app.deps import get_db, get_mb_client
from app.main import app
from app.musicbrainz.errors import MusicBrainzNotFound, MusicBrainzRetriesExhausted
from app.musicbrainz.models import (
    ArtistCredit,
    ArtistRef,
    ReleaseGroupSearchHit,
    ReleaseGroupSearchResult,
)
from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup
from app.services.catalog import PLACEHOLDER_COVER_PATH
from app.services.search import search_cache

pytestmark = pytest.mark.anyio

RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
COVER_URL = "https://coverartarchive.org/release-group/b1392450-e666-3926-a536-22c65f834433/front"


def _mb_release_group() -> MBReleaseGroup:
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


def _search_result() -> ReleaseGroupSearchResult:
    hit = ReleaseGroupSearchHit(
        id=RG_MBID,
        title="OK Computer",
        primary_type="Album",
        first_release_date="1997-05-21",
        score=100,
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
    return ReleaseGroupSearchResult(count=1, offset=0, release_groups=[hit])


@pytest.fixture
def mb_client() -> AsyncMock:
    client = AsyncMock()
    client.search_release_groups = AsyncMock(return_value=_search_result())
    client.get_release_group = AsyncMock(return_value=_mb_release_group())
    client.get_front_cover_url = AsyncMock(return_value=COVER_URL)
    return client


@pytest.fixture
async def client(
    session: Session, mb_client: AsyncMock
) -> AsyncIterator[AsyncClient]:
    search_cache.clear()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_mb_client] = lambda: mb_client

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    search_cache.clear()


async def test_search_returns_results(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get("/api/search", params={"q": "ok computer", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["limit"] == 10
    assert body["results"][0]["mbid"] == RG_MBID
    assert body["results"][0]["title"] == "OK Computer"
    assert body["results"][0]["artists"][0]["name"] == "Radiohead"
    mb_client.search_release_groups.assert_awaited_once_with(
        "ok computer", limit=10, offset=0
    )


async def test_search_cache_avoids_second_api_call(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    first = await client.get("/api/search", params={"q": "radiohead"})
    second = await client.get("/api/search", params={"q": "Radiohead"})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    mb_client.search_release_groups.assert_awaited_once()


async def test_search_musicbrainz_down_returns_503(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    mb_client.search_release_groups.side_effect = MusicBrainzRetriesExhausted(503, 5)

    response = await client.get("/api/search", params={"q": "ok computer"})

    assert response.status_code == 503
    assert "MusicBrainz is unavailable" in response.json()["detail"]
    assert "Traceback" not in response.text


async def test_get_album_fetches_and_returns_fields(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get(f"/api/albums/{RG_MBID}")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "mbid": RG_MBID,
        "title": "OK Computer",
        "artists": [
            {"mbid": ARTIST_MBID, "name": "Radiohead", "join_phrase": None}
        ],
        "first_release_date": "1997-05-21",
        "primary_type": "Album",
        "cover_url": COVER_URL,
    }
    mb_client.get_release_group.assert_awaited_once()
    mb_client.get_front_cover_url.assert_awaited_once()


async def test_get_album_missing_cover_uses_placeholder(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    mb_client.get_front_cover_url.return_value = None

    response = await client.get(f"/api/albums/{RG_MBID}")

    assert response.status_code == 200
    assert response.json()["cover_url"] == PLACEHOLDER_COVER_PATH


async def test_get_album_malformed_mbid_returns_404(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get("/api/albums/not-a-uuid")

    assert response.status_code == 404
    mb_client.get_release_group.assert_not_awaited()


async def test_get_album_unknown_mbid_returns_404(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    mb_client.get_release_group.side_effect = MusicBrainzNotFound("missing")

    response = await client.get(f"/api/albums/{unknown}")

    assert response.status_code == 404


async def test_get_album_musicbrainz_down_returns_503(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    mb_client.get_release_group.side_effect = MusicBrainzRetriesExhausted(503, 5)

    response = await client.get(f"/api/albums/{RG_MBID}")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "MusicBrainz is unavailable right now. Please try again later."
    )


async def test_get_album_uses_cache_on_second_request(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    first = await client.get(f"/api/albums/{RG_MBID}")
    second = await client.get(f"/api/albums/{RG_MBID}")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    mb_client.get_release_group.assert_awaited_once()
