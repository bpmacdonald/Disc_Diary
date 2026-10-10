"""Signup / login business logic (no HTTP concerns)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.security.passwords import (
    burn_dummy_verification,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.throttle import LoginThrottle

USERNAME_RE = re.compile(r"[A-Za-z0-9_]{5,30}")
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128  # bounds hashing work per request
EMAIL_MAX_LENGTH = 255

USERNAME_ERROR = (
    "Username must be 5-30 characters: letters, numbers, and underscores only."
)
EMAIL_ERROR = "Enter a valid email address."
PASSWORD_ERROR = (
    f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters."
)
USERNAME_TAKEN = "That username is already taken."
EMAIL_TAKEN = "An account with that email already exists."
LOGIN_ERROR = "Invalid username or password."

_CONSTRAINT_FIELDS = {
    "uq_users_username_lower": "username",
    "uq_users_email": "email",
}


class DuplicateUserError(Exception):
    def __init__(self, field_name: str) -> None:
        super().__init__(f"Duplicate {field_name}")
        self.field_name = field_name


@dataclass(frozen=True)
class SignupData:
    username: str
    email: str
    password: str


@dataclass
class SignupResult:
    user: User | None = None
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class LoginResult:
    user: User | None = None
    retry_after: int = 0  # seconds; > 0 means throttled


def _normalize_email(raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate or len(candidate) > EMAIL_MAX_LENGTH:
        return None
    try:
        checked = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None
    return checked.normalized.lower()


def validate_signup(
    username: str, email: str, password: str
) -> tuple[SignupData | None, dict[str, str]]:
    errors: dict[str, str] = {}
    clean_username = username.strip()
    if not USERNAME_RE.fullmatch(clean_username):
        errors["username"] = USERNAME_ERROR
    clean_email = _normalize_email(email)
    if clean_email is None:
        errors["email"] = EMAIL_ERROR
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        errors["password"] = PASSWORD_ERROR
    if errors or clean_email is None:
        return None, errors
    return SignupData(clean_username, clean_email, password), errors


def find_conflicts(session: Session, data: SignupData) -> dict[str, str]:
    conflicts: dict[str, str] = {}
    username_taken = session.scalar(
        select(User.id).where(func.lower(User.username) == data.username.lower())
    )
    if username_taken is not None:
        conflicts["username"] = USERNAME_TAKEN
    email_taken = session.scalar(select(User.id).where(User.email == data.email))
    if email_taken is not None:
        conflicts["email"] = EMAIL_TAKEN
    return conflicts


def _conflict_field(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return _CONSTRAINT_FIELDS.get(getattr(diag, "constraint_name", None) or "")


def create_user(session: Session, data: SignupData) -> User:
    """Insert a user. The unique indexes are the race-proof source of truth."""
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    try:
        with session.begin_nested():  # savepoint: a conflict leaves the session usable
            session.add(user)
            session.flush()
    except IntegrityError as exc:
        conflict = _conflict_field(exc)
        if conflict is None:
            raise
        raise DuplicateUserError(conflict) from exc
    return user


def signup(session: Session, username: str, email: str, password: str) -> SignupResult:
    data, errors = validate_signup(username, email, password)
    if data is None:
        return SignupResult(errors=errors)
    conflicts = find_conflicts(session, data)
    if conflicts:
        return SignupResult(errors=conflicts)
    try:
        user = create_user(session, data)
    except DuplicateUserError as exc:  # lost a race with a concurrent signup
        message = USERNAME_TAKEN if exc.field_name == "username" else EMAIL_TAKEN
        return SignupResult(errors={exc.field_name: message})
    # Commit before the redirect so the next request is guaranteed to see the user.
    session.commit()
    return SignupResult(user=user)


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(
        select(User).where(func.lower(User.username) == username.strip().lower())
    )
    if user is None:
        burn_dummy_verification(password)  # keep timing similar to a real check
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.commit()
    return user


def attempt_login(
    session: Session,
    throttle: LoginThrottle,
    *,
    ip: str,
    username: str,
    password: str,
) -> LoginResult:
    wait = throttle.retry_after(ip, username)
    if wait > 0:
        return LoginResult(retry_after=wait)
    if len(password) > PASSWORD_MAX_LENGTH:  # can't be valid; skip hashing work
        throttle.record_failure(ip, username)
        return LoginResult()
    user = authenticate(session, username, password)
    if user is None:
        throttle.record_failure(ip, username)
        return LoginResult()
    throttle.reset(ip, username)
    return LoginResult(user=user)
