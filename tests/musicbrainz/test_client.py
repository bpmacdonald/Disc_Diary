from collections.abc import Callable

import httpx
import pytest
import respx

from app.musicbrainz.client import MusicBrainzClient, parse_retry_after
from app.musicbrainz.errors import (
    MusicBrainzConfigError,
    MusicBrainzNotFound,
    MusicBrainzRetriesExhausted,
)
from app.musicbrainz.ratelimit import TokenBucket
from tests.musicbrainz.fakes import FakeClock

pytestmark = pytest.mark.anyio

BASE = "https://musicbrainz.org/ws/2"
UA = "DiscDiaryTest/0.0 (test@example.com)"
RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"

ARTIST_CREDIT = [
    {
        "name": "Radiohead",
        "joinphrase": "",
        "artist": {
            "id": ARTIST_MBID,
            "name": "Radiohead",
            "sort-name": "Radiohead",
            "disambiguation": "",
            "type": "Group",
        },
    }
]

SEARCH_RESPONSE = {
    "created": "2026-10-10T21:00:00.000Z",
    "count": 2,
    "offset": 0,
    "release-groups": [
        {
            "id": RG_MBID,
            "type-id": "f529b476-6e62-324f-b0aa-1f3e33d313fc",
            "score": 100,
            "primary-type": "Album",
            "title": "OK Computer",
            "first-release-date": "1997-05-21",
            "artist-credit": ARTIST_CREDIT,
            "releases": [{"id": "x", "title": "OK Computer"}],
            "tags": [{"count": 3, "name": "rock"}],
        },
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "score": 60,
            "title": "Untitled",
            "first-release-date": "",
            "artist-credit": [],
        },
    ],
}

LOOKUP_RESPONSE = {
    "id": RG_MBID,
    "title": "OK Computer",
    "primary-type": "Album",
    "first-release-date": "1997-05-21",
    "artist-credit": ARTIST_CREDIT,
    "tags": [{"count": 5, "name": "alternative rock"}, {"count": 2, "name": "art rock"}],
}

ARTIST_RESPONSE = {
    "id": ARTIST_MBID,
    "name": "Radiohead",
    "sort-name": "Radiohead",
    "type": "Group",
    "country": "GB",
    "life-span": {"begin": "1985"},
}


class Harness:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.retry_sleeps: list[float] = []

    async def _retry_sleep(self, seconds: float) -> None:
        self.retry_sleeps.append(seconds)
        await self.clock.sleep(seconds)

    def client(self, **kwargs: object) -> MusicBrainzClient:
        limiter = TokenBucket(clock=self.clock.time, sleep=self.clock.sleep)
        return MusicBrainzClient(
            UA, limiter=limiter, sleep=self._retry_sleep, **kwargs  # type: ignore[arg-type]
        )


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def make_client(harness: Harness) -> Callable[..., MusicBrainzClient]:
    return harness.client


# ---- configuration -------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_user_agent_refused(bad: str) -> None:
    with pytest.raises(MusicBrainzConfigError):
        MusicBrainzClient(bad)


def test_missing_user_agent_setting_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MB_USER_AGENT", raising=False)
    monkeypatch.chdir("tests")  # so no .env file is picked up
    import sys

    sys.modules.pop("app.config", None)
    with pytest.raises(MusicBrainzConfigError):
        MusicBrainzClient()


async def test_request_sends_user_agent_and_fmt_json(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    route = respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").respond(json=ARTIST_RESPONSE)
    async with make_client() as client:
        await client.get_artist(ARTIST_MBID)

    request = route.calls.last.request
    assert request.headers["user-agent"] == UA
    assert request.url.params["fmt"] == "json"


# ---- parsing -------------------------------------------------------


async def test_search_parses_realistic_response(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    route = respx_mock.get(f"{BASE}/release-group").respond(json=SEARCH_RESPONSE)
    async with make_client() as client:
        result = await client.search_release_groups("ok computer", limit=2, offset=0)

    params = route.calls.last.request.url.params
    assert params["query"] == "ok computer"
    assert params["limit"] == "2"
    assert params["offset"] == "0"

    assert result.count == 2
    first, second = result.release_groups
    assert first.id == RG_MBID
    assert first.title == "OK Computer"
    assert first.primary_type == "Album"
    assert first.first_release_date == "1997-05-21"
    assert first.score == 100
    assert first.artist_credit[0].artist.name == "Radiohead"
    assert first.artist_credit[0].artist.sort_name == "Radiohead"
    assert first.artist_credit[0].join_phrase == ""
    # Missing / empty optional fields become None or empty lists.
    assert second.primary_type is None
    assert second.first_release_date is None
    assert second.artist_credit == []


async def test_get_release_group_requests_artists_and_tags(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    route = respx_mock.get(f"{BASE}/release-group/{RG_MBID}").respond(json=LOOKUP_RESPONSE)
    async with make_client() as client:
        rg = await client.get_release_group(RG_MBID.upper())  # normalised to lowercase

    assert route.calls.last.request.url.params["inc"] == "artists+tags"
    assert [t.name for t in rg.tags] == ["alternative rock", "art rock"]
    assert rg.artist_credit[0].artist.id == ARTIST_MBID


async def test_get_artist_parses_fields(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").respond(json=ARTIST_RESPONSE)
    async with make_client() as client:
        artist = await client.get_artist(ARTIST_MBID)

    assert (artist.name, artist.type, artist.country) == ("Radiohead", "Group", "GB")


async def test_invalid_mbid_rejected_without_request(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    async with make_client() as client:
        with pytest.raises(ValueError):
            await client.get_artist("../../etc/passwd")
    assert respx_mock.calls.call_count == 0


# ---- errors --------------------------------------------------------


async def test_unknown_mbid_raises_not_found(
    respx_mock: respx.MockRouter, make_client: Callable[..., MusicBrainzClient]
) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    respx_mock.get(f"{BASE}/release-group/{unknown}").respond(
        404, json={"error": "Not Found"}
    )
    async with make_client() as client:
        with pytest.raises(MusicBrainzNotFound):
            await client.get_release_group(unknown)


# ---- retry ---------------------------------------------------------


async def test_retries_503_with_exponential_backoff(
    respx_mock: respx.MockRouter, harness: Harness
) -> None:
    route = respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=ARTIST_RESPONSE),
        ]
    )
    async with harness.client() as client:
        artist = await client.get_artist(ARTIST_MBID)

    assert artist.name == "Radiohead"
    assert route.call_count == 3
    assert harness.retry_sleeps == [1.0, 2.0]


async def test_retry_after_header_is_respected(
    respx_mock: respx.MockRouter, harness: Harness
) -> None:
    respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=ARTIST_RESPONSE),
        ]
    )
    async with harness.client() as client:
        await client.get_artist(ARTIST_MBID)

    assert harness.retry_sleeps == [7.0]


async def test_retry_after_beyond_cap_gives_up(
    respx_mock: respx.MockRouter, harness: Harness
) -> None:
    respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").respond(
        429, headers={"Retry-After": "9999"}
    )
    async with harness.client() as client:
        with pytest.raises(MusicBrainzRetriesExhausted):
            await client.get_artist(ARTIST_MBID)
    assert harness.retry_sleeps == []


async def test_retries_exhausted_raises_clear_error(
    respx_mock: respx.MockRouter, harness: Harness
) -> None:
    route = respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").respond(503)
    async with harness.client(max_retries=2) as client:
        with pytest.raises(MusicBrainzRetriesExhausted) as info:
            await client.get_artist(ARTIST_MBID)

    assert route.call_count == 3
    assert info.value.status_code == 503
    assert info.value.attempts == 3


async def test_every_attempt_goes_through_the_rate_limiter(
    respx_mock: respx.MockRouter, harness: Harness
) -> None:
    stamps: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        stamps.append(harness.clock.now)
        return httpx.Response(503 if len(stamps) < 3 else 200, json=ARTIST_RESPONSE)

    respx_mock.get(f"{BASE}/artist/{ARTIST_MBID}").mock(side_effect=record)
    async with harness.client(backoff_base=0.1) as client:  # backoff shorter than 1s
        await client.get_artist(ARTIST_MBID)

    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(gap >= 1.0 for gap in gaps)


# ---- helpers -------------------------------------------------------


def test_parse_retry_after_variants() -> None:
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # past date
