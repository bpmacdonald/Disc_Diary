import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_mb_client
from app.main import create_app
from app.models import Log, ReleaseGroup, User
from app.musicbrainz.models import ArtistCredit, ArtistRef
from app.musicbrainz.models import ReleaseGroup as MBReleaseGroup
from app.security.passwords import hash_password
from app.services.search import search_cache

pytestmark = pytest.mark.anyio

RG_MBID = "b1392450-e666-3926-a536-22c65f834433"
ARTIST_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
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
    client.get_release_group = AsyncMock(return_value=_mb_rg())
    client.get_front_cover_url = AsyncMock(return_value=None)
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


def _token(response: Response) -> str:
    match = CSRF_RE.search(response.text)
    assert match, "CSRF token missing"
    return match.group(1)


def _user(session: Session, username: str, email: str) -> User:
    user = User(
        username=username, email=email, password_hash=hash_password(PASSWORD)
    )
    session.add(user)
    session.flush()
    return user


def _seed_album(session: Session) -> ReleaseGroup:
    rg = ReleaseGroup(
        mbid=RG_MBID,
        title="OK Computer",
        primary_type="Album",
        first_release_date="1997-05-21",
        cover_url=None,
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(rg)
    session.flush()
    return rg


async def _login(client: AsyncClient, username: str) -> None:
    client.cookies.clear()
    token = _token(await client.get("/login"))
    response = await client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf_token": token},
    )
    assert response.status_code == 303


async def _create_log(
    client: AsyncClient,
    *,
    rating: str = "8",
    review: str = "Classic",
    listened_on: str | None = None,
    is_relisten: bool = False,
    headers: dict[str, str] | None = None,
) -> Response:
    page = await client.get(f"/albums/{RG_MBID}")
    data = {
        "csrf_token": _token(page),
        "rating": rating,
        "review_text": review,
        "listened_on": listened_on or date.today().isoformat(),
    }
    if is_relisten:
        data["is_relisten"] = "on"
    return await client.post(
        f"/albums/{RG_MBID}/logs",
        data=data,
        headers=headers or {"HX-Request": "true"},
    )


async def test_create_log_via_htmx(client: AsyncClient, session: Session) -> None:
    alice = _user(session, "alice_01", "a@example.com")
    await _login(client, "alice_01")

    response = await _create_log(client, rating="7", review="Still perfect")

    assert response.status_code == 200
    assert 'id="album-logging"' in response.text
    assert "Still perfect" in response.text
    assert "★★★½" in response.text
    assert "Saved your listen." in response.text
    log = session.scalars(select(Log)).one()
    assert log.user_id == alice.id
    assert log.rating == 7
    assert log.review_text == "Still perfect"
    assert log.is_relisten is False


async def test_edit_own_log(client: AsyncClient, session: Session) -> None:
    _user(session, "alice_01", "a@example.com")
    await _login(client, "alice_01")
    await _create_log(client, rating="6", review="OK")
    log = session.scalars(select(Log)).one()

    edit_page = await client.get(
        f"/logs/{log.id}/edit", headers={"HX-Request": "true"}
    )
    assert edit_page.status_code == 200
    assert "Edit your listen" in edit_page.text

    response = await client.post(
        f"/logs/{log.id}",
        data={
            "csrf_token": _token(edit_page),
            "rating": "10",
            "review_text": "Masterpiece",
            "listened_on": date.today().isoformat(),
            "is_relisten": "on",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Masterpiece" in response.text
    assert "★★★★★" in response.text
    session.refresh(log)
    assert log.rating == 10
    assert log.is_relisten is True


async def test_delete_own_log(client: AsyncClient, session: Session) -> None:
    _user(session, "alice_01", "a@example.com")
    await _login(client, "alice_01")
    await _create_log(client)
    log_id = session.scalars(select(Log.id)).one()
    page = await client.get(f"/albums/{RG_MBID}")

    response = await client.post(
        f"/logs/{log_id}/delete",
        data={"csrf_token": _token(page)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Deleted your listen." in response.text
    assert session.scalar(select(func.count()).select_from(Log)) == 0


async def test_validation_errors_return_friendly_messages(
    client: AsyncClient, session: Session
) -> None:
    _user(session, "alice_01", "a@example.com")
    await _login(client, "alice_01")
    page = await client.get(f"/albums/{RG_MBID}")

    response = await client.post(
        f"/albums/{RG_MBID}/logs",
        data={
            "csrf_token": _token(page),
            "rating": "0",
            "review_text": "x" * 5001,
            "listened_on": "2099-01-01",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 422
    assert "half stars" in response.text or "rating" in response.text.lower()
    assert "5000" in response.text
    assert "future" in response.text
    assert session.scalar(select(func.count()).select_from(Log)) == 0


async def test_user_cannot_edit_or_delete_another_users_log(
    client: AsyncClient, session: Session
) -> None:
    alice = _user(session, "alice_01", "a@example.com")
    bob = _user(session, "bob_0002", "b@example.com")
    rg = _seed_album(session)
    log = Log(
        user_id=alice.id,
        release_group_id=rg.id,
        rating=8,
        review_text="Alice's log",
        listened_on=date.today(),
        is_relisten=False,
    )
    session.add(log)
    session.flush()
    log_id = log.id

    await _login(client, "bob_0002")

    edit = await client.get(f"/logs/{log_id}/edit")
    assert edit.status_code == 404
    assert "403" not in edit.text
    assert "Alice's log" not in edit.text

    page = await client.get(f"/albums/{RG_MBID}")
    token = _token(page)

    update = await client.post(
        f"/logs/{log_id}",
        data={
            "csrf_token": token,
            "rating": "1",
            "review_text": "hijacked",
            "listened_on": date.today().isoformat(),
        },
    )
    assert update.status_code == 404

    delete = await client.post(
        f"/logs/{log_id}/delete", data={"csrf_token": token}
    )
    assert delete.status_code == 404

    session.refresh(log)
    assert log.review_text == "Alice's log"
    assert log.rating == 8
    assert session.get(User, bob.id) is not None


async def test_anonymous_user_is_sent_to_login(
    client: AsyncClient, session: Session
) -> None:
    _seed_album(session)
    page = await client.get(f"/albums/{RG_MBID}")
    assert "Log in" in page.text and "log this album" in page.text

    response = await client.post(
        f"/albums/{RG_MBID}/logs",
        data={
            "csrf_token": _token(page),
            "rating": "5",
            "review_text": "nope",
            "listened_on": date.today().isoformat(),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers["hx-redirect"].startswith("/login?")
    assert session.scalar(select(func.count()).select_from(Log)) == 0


async def test_album_page_shows_average_and_only_owner_actions(
    client: AsyncClient, session: Session
) -> None:
    alice = _user(session, "alice_01", "a@example.com")
    bob = _user(session, "bob_0002", "b@example.com")
    rg = _seed_album(session)
    session.add_all(
        [
            Log(
                user_id=alice.id,
                release_group_id=rg.id,
                rating=10,
                review_text="Alice",
                listened_on=date.today(),
                is_relisten=False,
            ),
            Log(
                user_id=bob.id,
                release_group_id=rg.id,
                rating=6,
                review_text="Bob",
                listened_on=date.today(),
                is_relisten=True,
            ),
        ]
    )
    session.flush()

    await _login(client, "alice_01")
    page = await client.get(f"/albums/{RG_MBID}")
    assert page.status_code == 200
    assert "4.0 / 5" in page.text or "★★★★" in page.text
    assert "Alice" in page.text and "Bob" in page.text
    # Alice can edit her log, not Bob's.
    assert page.text.count("hx-get=\"/logs/") == 1
    assert "Delete" in page.text
