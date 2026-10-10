from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Log, ReleaseGroup, User


def _user(username: str = "ben", email: str = "ben@example.com") -> User:
    return User(username=username, email=email, password_hash="x")


def _release_group(mbid: str = "00000000-0000-0000-0000-000000000001") -> ReleaseGroup:
    return ReleaseGroup(
        mbid=mbid, title="OK Computer", fetched_at=datetime.now(timezone.utc)
    )


def test_username_unique_case_insensitive(session: Session) -> None:
    session.add(_user("Ben", "a@example.com"))
    session.flush()
    session.add(_user("bEN", "b@example.com"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_album_can_be_logged_twice(session: Session) -> None:
    user, rg = _user(), _release_group()
    session.add_all([user, rg])
    session.flush()
    for relisten in (False, True):
        session.add(
            Log(
                user_id=user.id,
                release_group_id=rg.id,
                listened_on=date(2026, 1, 1),
                is_relisten=relisten,
            )
        )
    session.flush()


@pytest.mark.parametrize("rating", [0, 11])
def test_rating_out_of_range_rejected(session: Session, rating: int) -> None:
    user, rg = _user(), _release_group()
    session.add_all([user, rg])
    session.flush()
    session.add(
        Log(
            user_id=user.id,
            release_group_id=rg.id,
            rating=rating,
            listened_on=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
