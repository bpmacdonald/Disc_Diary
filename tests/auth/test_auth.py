import re
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_login_throttle
from app.main import create_app
from app.models import User
from app.security.passwords import hash_password, verify_password
from app.security.throttle import LoginThrottle
from app.services.auth import (
    DuplicateUserError,
    LOGIN_ERROR,
    SignupData,
    create_user,
)

pytestmark = pytest.mark.anyio

PASSWORD = "correct-horse-battery"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _settings(debug: bool = False) -> Settings:
    return Settings(
        postgres_user="x",
        postgres_password="x",
        postgres_db="x",
        database_url="postgresql+psycopg://x:x@localhost/x",
        mb_user_agent="Test/0.0 (test@example.com)",
        secret_key="k" * 48,
        debug=debug,
    )


@pytest.fixture
async def client(session: Session) -> AsyncIterator[AsyncClient]:
    application = create_app(_settings(debug=False))

    def override_db() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db] = override_db
    throttle = LoginThrottle()  # fresh per test
    application.dependency_overrides[get_login_throttle] = lambda: throttle
    # https base URL so the Secure session cookie is sent back by the cookie jar.
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://test"
    ) as ac:
        yield ac


def _token(response: Response) -> str:
    match = CSRF_RE.search(response.text)
    assert match, "CSRF token not found in page"
    return match.group(1)


async def _signup(
    client: AsyncClient,
    username: str = "alice_01",
    email: str = "alice@example.com",
    password: str = PASSWORD,
) -> Response:
    client.cookies.clear()  # act as a fresh anonymous visitor
    token = _token(await client.get("/signup"))
    return await client.post(
        "/signup",
        data={"username": username, "email": email, "password": password, "csrf_token": token},
    )


async def _login(
    client: AsyncClient, username: str = "alice_01", password: str = PASSWORD
) -> Response:
    token = _token(await client.get("/login"))
    return await client.post(
        "/login", data={"username": username, "password": password, "csrf_token": token}
    )


# ---- signup --------------------------------------------------------


async def test_signup_creates_user_logs_in_and_sets_secure_cookie(
    client: AsyncClient, session: Session
) -> None:
    response = await _signup(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie

    user = session.scalars(select(User)).one()
    assert user.email == "alice@example.com"
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert "Hi, alice_01" in (await client.get("/account")).text


async def test_duplicate_email_rejected(client: AsyncClient, session: Session) -> None:
    await _signup(client)
    other = await _signup(client, username="bob_0002", email="ALICE@example.com")

    assert other.status_code == 400
    assert "An account with that email already exists." in other.text
    assert session.scalar(select(func.count()).select_from(User)) == 1


async def test_duplicate_username_rejected_case_insensitively(
    client: AsyncClient, session: Session
) -> None:
    await _signup(client)
    other = await _signup(client, username="ALICE_01", email="other@example.com")

    assert other.status_code == 400
    assert "That username is already taken." in other.text
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_create_user_constraint_backstop_raises_duplicate(session: Session) -> None:
    create_user(session, SignupData("alice_01", "a@example.com", PASSWORD))
    with pytest.raises(DuplicateUserError) as info:
        create_user(session, SignupData("Alice_01", "b@example.com", PASSWORD))
    assert info.value.field_name == "username"
    # The savepoint keeps the session usable after the conflict.
    create_user(session, SignupData("carol_03", "c@example.com", PASSWORD))


@pytest.mark.parametrize(
    ("username", "email", "password", "field"),
    [
        ("abcd", "a@example.com", PASSWORD, "Username must be"),
        ("a" * 31, "a@example.com", PASSWORD, "Username must be"),
        ("bad name!", "a@example.com", PASSWORD, "Username must be"),
        ("alice_01", "not-an-email", PASSWORD, "valid email"),
        ("alice_01", "a@example.com", "short", "Password must be"),
    ],
)
async def test_signup_validation_errors(
    client: AsyncClient,
    session: Session,
    username: str,
    email: str,
    password: str,
    field: str,
) -> None:
    response = await _signup(client, username, email, password)

    assert response.status_code == 400
    assert field in response.text
    assert password not in response.text or password == ""
    assert session.scalar(select(func.count()).select_from(User)) == 0


async def test_template_escapes_user_input(client: AsyncClient) -> None:
    response = await _signup(client, username="<script>alert(1)</script>")

    assert response.status_code == 400
    assert "<script>alert(1)</script>" not in response.text


# ---- login ---------------------------------------------------------


async def test_wrong_password_and_unknown_user_look_identical(
    client: AsyncClient, session: Session
) -> None:
    session.add(User(username="alice_01", email="a@example.com", password_hash=hash_password(PASSWORD)))
    session.flush()

    wrong = await _login(client, "alice_01", "wrong-password-123")
    unknown = await _login(client, "nobody_99", "wrong-password-123")

    assert wrong.status_code == unknown.status_code == 401
    assert LOGIN_ERROR in wrong.text and LOGIN_ERROR in unknown.text
    # Same visible error: no hint about which usernames exist.
    assert re.sub(CSRF_RE, "", wrong.text) == re.sub(CSRF_RE, "", unknown.text).replace(
        "nobody_99", "alice_01"
    )
    # A failed login never authenticates the visitor.
    assert (await client.get("/account")).status_code == 303


async def test_login_success_redirects_and_open_redirect_is_blocked(
    client: AsyncClient, session: Session
) -> None:
    session.add(User(username="alice_01", email="a@example.com", password_hash=hash_password(PASSWORD)))
    session.flush()

    token = _token(await client.get("/login?next=//evil.com"))
    response = await client.post(
        "/login",
        data={"username": "alice_01", "password": PASSWORD, "csrf_token": token, "next": "//evil.com"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/account"


async def test_repeated_failures_are_throttled_even_with_correct_password(
    client: AsyncClient, session: Session
) -> None:
    session.add(User(username="alice_01", email="a@example.com", password_hash=hash_password(PASSWORD)))
    session.flush()

    for _ in range(5):
        assert (await _login(client, "alice_01", "wrong-password-123")).status_code == 401
    blocked = await _login(client, "alice_01", PASSWORD)

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert "Too many failed attempts" in blocked.text


# ---- logout / protected routes -------------------------------------


async def test_logout_clears_session(client: AsyncClient) -> None:
    await _signup(client)
    account = await client.get("/account")
    assert account.status_code == 200

    response = await client.post("/logout", data={"csrf_token": _token(account)})

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    after = await client.get("/account")
    assert after.status_code == 303
    assert after.headers["location"].startswith("/login")


async def test_protected_route_redirects_to_login(client: AsyncClient) -> None:
    response = await client.get("/account")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/account"


# ---- CSRF ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/signup", "/login", "/logout"])
async def test_post_without_csrf_token_is_rejected(
    client: AsyncClient, path: str
) -> None:
    await client.get("/login")  # establish a session
    response = await client.post(
        path, data={"username": "alice_01", "email": "a@example.com", "password": PASSWORD}
    )
    assert response.status_code == 403


async def test_post_with_wrong_csrf_token_is_rejected(client: AsyncClient) -> None:
    await client.get("/login")
    response = await client.post(
        "/login", data={"username": "a", "password": "b", "csrf_token": "forged"}
    )
    assert response.status_code == 403


async def test_logout_with_forged_token_keeps_session(client: AsyncClient) -> None:
    await _signup(client)
    response = await client.post("/logout", data={"csrf_token": "forged"})

    assert response.status_code == 403
    assert (await client.get("/account")).status_code == 200


# ---- cookie flags in debug -----------------------------------------


async def test_cookie_not_secure_in_debug_mode(session: Session) -> None:
    application = create_app(_settings(debug=True))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as ac:
        response = await ac.get("/login")

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie


def test_password_hash_roundtrip() -> None:
    hashed = hash_password(PASSWORD)
    assert verify_password(hashed, PASSWORD)
    assert not verify_password(hashed, "wrong")
    assert not verify_password("not-a-hash", PASSWORD)
