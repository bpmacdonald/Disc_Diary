from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import User
from app.musicbrainz.client import MusicBrainzClient, get_musicbrainz_client
from app.security.session import end_session, session_user_id
from app.security.throttle import LoginThrottle, login_throttle

_engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


class LoginRequired(Exception):
    """Raised by require_user; handled by redirecting to the login page."""

    def __init__(self, next_path: str) -> None:
        super().__init__("Login required")
        self.next_path = next_path


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_mb_client() -> MusicBrainzClient:
    return get_musicbrainz_client()


def get_login_throttle() -> LoginThrottle:
    return login_throttle


def get_current_user(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> User | None:
    user_id = session_user_id(request)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:  # account deleted since the cookie was issued
        end_session(request)
    return user


def require_user(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> User:
    if user is None:
        raise LoginRequired(request.url.path)
    return user
