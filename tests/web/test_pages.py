from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_mb_client
from app.main import create_app
from app.musicbrainz.errors import MusicBrainzRetriesExhausted
from app.musicbrainz.models import (
    ArtistCredit,
    ArtistRef,
    Medium,
    ReleaseBrowseResult,
    ReleaseDetail,
    ReleaseGroupSearchHit,
    ReleaseGroupSearchResult,
    Track,
)
from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup
from app.services.catalog import PLACEHOLDER_COVER_PATH
from app.services.search import search_cache

pytestmark = pytest.mark.anyio

RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
REL_MBID = "0b626195-faad-3b4c-8fc6-efb8950d2770"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


def _settings() -> Settings:
    return Settings(
        postgres_user="x",
        postgres_password="x",
        postgres_db="x",
        database_url="postgresql+psycopg://x:x@localhost/x",
        mb_user_agent="Test/0.0 (test@example.com)",
        secret_key="k" * 48,
        debug=True,
    )


def _mb_rg() -> MBReleaseGroup:
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


@pytest.fixture
def mb_client() -> AsyncMock:
    client = AsyncMock()
    client.search_release_groups = AsyncMock(
        return_value=ReleaseGroupSearchResult(
            count=1,
            offset=0,
            release_groups=[
                ReleaseGroupSearchHit(
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
                                id=ARTIST_MBID,
                                name="Radiohead",
                                sort_name="Radiohead",
                            ),
                        )
                    ],
                )
            ],
        )
    )
    client.get_release_group = AsyncMock(return_value=_mb_rg())
    client.get_front_cover_url = AsyncMock(return_value=None)
    client.browse_releases_for_group = AsyncMock(
        return_value=ReleaseBrowseResult.model_validate(
            {
                "release-count": 1,
                "release-offset": 0,
                "releases": [
                    {
                        "id": REL_MBID,
                        "title": "OK Computer",
                        "status": "Official",
                        "country": "GB",
                        "date": "1997-05-21",
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
                        Track(
                            position=2,
                            number="2",
                            title="Paranoid Android",
                            length=383000,
                        ),
                    ],
                )
            ],
        )
    )
    return client


@pytest.fixture
async def client(
    session: Session, mb_client: AsyncMock
) -> AsyncIterator[AsyncClient]:
    search_cache.clear()
    application = create_app(_settings())

    def override_db() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_mb_client] = lambda: mb_client

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        yield ac

    application.dependency_overrides.clear()
    search_cache.clear()


async def test_search_page_renders_nav_and_htmx_input(client: AsyncClient) -> None:
    response = await client.get("/search")

    assert response.status_code == 200
    assert "Disc Diary" in response.text
    assert 'hx-get="/search/results"' in response.text
    assert "delay:300ms" in response.text
    assert "Log in" in response.text and "Sign up" in response.text
    assert "picocss" in response.text
    assert "htmx.org" in response.text


async def test_search_results_partial_renders_cards(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get(
        "/search/results",
        params={"q": "ok computer"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "OK Computer" in response.text
    assert "Radiohead" in response.text
    assert "1997" in response.text
    assert f'href="/albums/{RG_MBID}"' in response.text
    assert "coverartarchive.org" in response.text
    assert PLACEHOLDER_COVER_PATH in response.text  # onerror fallback
    mb_client.search_release_groups.assert_awaited_once()


async def test_search_results_short_query_skips_api(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get("/search/results", params={"q": "a"})

    assert response.status_code == 200
    assert "at least 2 characters" in response.text
    mb_client.search_release_groups.assert_not_awaited()


async def test_album_page_shows_details_and_placeholders(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get(f"/albums/{RG_MBID}")

    assert response.status_code == 200
    assert "OK Computer" in response.text
    assert "Radiohead" in response.text
    assert "1997-05-21" in response.text
    assert "Album" in response.text
    assert "No ratings yet" in response.text
    assert "No recent listens yet" in response.text
    assert "Show tracklist" in response.text
    assert f'hx-get="/albums/{RG_MBID}/tracks"' in response.text
    assert PLACEHOLDER_COVER_PATH in response.text
    mb_client.get_release_group.assert_awaited()


async def test_tracklist_partial_loads_on_demand(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    response = await client.get(
        f"/albums/{RG_MBID}/tracks", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert "Airbag" in response.text
    assert "Paranoid Android" in response.text
    assert "4:44" in response.text
    mb_client.browse_releases_for_group.assert_awaited()
    mb_client.get_release.assert_awaited_once_with(REL_MBID)

    # Second load uses the DB cache.
    mb_client.browse_releases_for_group.reset_mock()
    mb_client.get_release.reset_mock()
    again = await client.get(f"/albums/{RG_MBID}/tracks")
    assert again.status_code == 200
    assert "Airbag" in again.text
    mb_client.browse_releases_for_group.assert_not_awaited()
    mb_client.get_release.assert_not_awaited()


async def test_album_page_musicbrainz_down_is_html_503(
    client: AsyncClient, mb_client: AsyncMock
) -> None:
    mb_client.get_release_group.side_effect = MusicBrainzRetriesExhausted(503, 5)

    response = await client.get(f"/albums/{RG_MBID}")

    assert response.status_code == 503
    assert "MusicBrainz is unavailable" in response.text
    assert "Traceback" not in response.text


async def test_home_redirects_to_search(client: AsyncClient) -> None:
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/search"
