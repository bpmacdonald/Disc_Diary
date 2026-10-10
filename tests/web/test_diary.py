import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.orm import Session

from fastapi import HTTPException
from starlette.requests import Request

from app.config import Settings
from app.deps import get_db
from app.main import _unhandled_error, create_app
from app.models import Artist, Log, ReleaseGroup, ReleaseGroupArtist, User
from app.security.passwords import hash_password
from app.services.diary import DIARY_PAGE_SIZE, DiaryEntry, diary_for_user, group_entries_by_month

pytestmark = pytest.mark.anyio

PASSWORD = "correct-horse-battery"
CSRF_RE = re.compile(
    r'(?:name="csrf_token" value="|name="csrf-token" content=")([^"]+)"'
)


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


@pytest.fixture
async def client(session: Session) -> AsyncIterator[AsyncClient]:
    application = create_app(_settings())

    @application.get("/__boom__")
    def boom() -> None:
        raise HTTPException(status_code=500, detail="intentional test failure")

    def override_db() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db] = override_db

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        yield ac

    application.dependency_overrides.clear()


def _token(response: Response) -> str:
    match = CSRF_RE.search(response.text)
    assert match
    return match.group(1)


def _user(session: Session, username: str = "alice_01", email: str = "a@example.com") -> User:
    user = User(username=username, email=email, password_hash=hash_password(PASSWORD))
    session.add(user)
    session.flush()
    return user


def _album(session: Session, n: int, artist_name: str = "Radiohead") -> ReleaseGroup:
    artist = Artist(
        mbid=f"aaaaaaaa-bbbb-cccc-dddd-{n:012d}",
        name=artist_name,
        sort_name=artist_name,
        fetched_at=datetime.now(timezone.utc),
    )
    rg = ReleaseGroup(
        mbid=f"bbbbbbbb-cccc-dddd-eeee-{n:012d}",
        title=f"Album {n:02d}",
        primary_type="Album",
        first_release_date="1997-05-21",
        cover_url=None,
        fetched_at=datetime.now(timezone.utc),
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


async def _login(client: AsyncClient, username: str = "alice_01") -> None:
    client.cookies.clear()
    token = _token(await client.get("/login"))
    response = await client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf_token": token},
    )
    assert response.status_code == 303


def test_group_entries_by_month_keeps_newest_first() -> None:
    entries = [
        DiaryEntry(
            log_id=1,
            listened_on=date(2026, 10, 9),
            rating=8,
            stars="★★★★",
            is_relisten=False,
            review_text=None,
            album_mbid="x",
            album_title="A",
            artists="Artist",
            cover_url="/static/images/cover-placeholder.svg",
        ),
        DiaryEntry(
            log_id=2,
            listened_on=date(2026, 10, 1),
            rating=None,
            stars="",
            is_relisten=False,
            review_text=None,
            album_mbid="y",
            album_title="B",
            artists="Artist",
            cover_url="/static/images/cover-placeholder.svg",
        ),
        DiaryEntry(
            log_id=3,
            listened_on=date(2026, 9, 15),
            rating=5,
            stars="★★½",
            is_relisten=True,
            review_text=None,
            album_mbid="z",
            album_title="C",
            artists="Artist",
            cover_url="/static/images/cover-placeholder.svg",
        ),
    ]
    groups = group_entries_by_month(entries)
    assert [g.label for g in groups] == ["October 2026", "September 2026"]
    assert [e.log_id for e in groups[0].entries] == [1, 2]


async def test_diary_pagination_and_month_grouping(
    client: AsyncClient, session: Session
) -> None:
    user = _user(session)
    # 26 listens across two months → page 1 has 25, page 2 has 1.
    for i in range(DIARY_PAGE_SIZE + 1):
        rg = _album(session, i + 1)
        listened = date(2026, 10, 10) - timedelta(days=i)
        session.add(
            Log(
                user_id=user.id,
                release_group_id=rg.id,
                rating=8 if i % 2 == 0 else None,
                review_text=None,
                listened_on=listened,
                is_relisten=False,
            )
        )
    session.flush()

    page = diary_for_user(session, user.id, page=1)
    assert page.pagination.total == DIARY_PAGE_SIZE + 1
    assert page.pagination.total_pages == 2
    assert sum(len(g.entries) for g in page.groups) == DIARY_PAGE_SIZE
    assert page.groups[0].label  # month heading present

    page2 = diary_for_user(session, user.id, page=2)
    assert sum(len(g.entries) for g in page2.groups) == 1

    await _login(client)
    response = await client.get("/diary")
    assert response.status_code == 200
    assert "Your diary" in response.text
    assert "October 2026" in response.text or "September 2026" in response.text
    assert "Album 01" in response.text
    assert "Radiohead" in response.text
    assert "Edit" in response.text
    assert "Page 1 of 2" in response.text
    assert 'rel="next"' in response.text

    page2_html = await client.get("/diary?page=2")
    assert page2_html.status_code == 200
    assert "Page 2 of 2" in page2_html.text
    assert 'rel="prev"' in page2_html.text


async def test_diary_requires_login(client: AsyncClient) -> None:
    response = await client.get("/diary")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?")


async def test_public_profile_shows_counts_and_recent(
    client: AsyncClient, session: Session
) -> None:
    user = _user(session, "alice_01", "a@example.com")
    rg_a = _album(session, 1, "Artist A")
    rg_b = _album(session, 2, "Artist B")
    session.add_all(
        [
            Log(
                user_id=user.id,
                release_group_id=rg_a.id,
                rating=10,
                review_text="Loved it",
                listened_on=date(2026, 10, 1),
                is_relisten=False,
            ),
            Log(
                user_id=user.id,
                release_group_id=rg_a.id,
                rating=None,
                review_text=None,
                listened_on=date(2026, 10, 5),
                is_relisten=True,
            ),
            Log(
                user_id=user.id,
                release_group_id=rg_b.id,
                rating=6,
                review_text=None,
                listened_on=date(2026, 9, 1),
                is_relisten=False,
            ),
        ]
    )
    session.flush()

    response = await client.get("/u/alice_01")
    assert response.status_code == 200
    assert "<h1>alice_01</h1>" in response.text
    assert "<strong>3</strong> listens" in response.text
    assert "<strong>2</strong> albums" in response.text
    assert "<strong>2</strong> rated" in response.text
    assert "Album 01" in response.text
    assert "Artist A" in response.text
    # Public profile has no edit links.
    assert "Edit" not in response.text


async def test_profile_nonexistent_user_returns_custom_404(
    client: AsyncClient,
) -> None:
    response = await client.get("/u/nobody_here")
    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "nobody_here" in response.text
    assert "Disc Diary" in response.text  # layout chrome
    assert "Back to search" in response.text


async def test_unknown_path_uses_custom_404(client: AsyncClient) -> None:
    response = await client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "picocss" in response.text


async def test_http_500_uses_custom_page(client: AsyncClient) -> None:
    response = await client.get("/__boom__")
    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert "Disc Diary" in response.text
    assert "Traceback" not in response.text


async def test_unhandled_error_handler_renders_500_layout() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "headers": [],
        "client": ("test", 50000),
        "server": ("test", 80),
        "session": {},
    }
    request = Request(scope)
    response = await _unhandled_error(request, RuntimeError("boom"))
    body = response.body.decode()
    assert response.status_code == 500
    assert "Something went wrong" in body
    assert "Disc Diary" in body
