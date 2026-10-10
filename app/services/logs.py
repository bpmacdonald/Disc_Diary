"""Create, update, and list album listen logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Log, ReleaseGroup, User

REVIEW_MAX_LENGTH = 5000
RECENT_LOGS_LIMIT = 20

RATING_ERROR = "Choose a rating between ½ and 5 stars, or leave it blank."
REVIEW_ERROR = f"Review must be at most {REVIEW_MAX_LENGTH} characters."
DATE_INVALID_ERROR = "Enter a valid date (YYYY-MM-DD)."
DATE_FUTURE_ERROR = "Listened-on can't be in the future."


class LogNotFound(Exception):
    """Missing log, or a log that does not belong to the caller (always 404)."""


@dataclass
class LogFormValues:
    rating: str = ""
    review_text: str = ""
    listened_on: str = ""
    is_relisten: bool = False


@dataclass
class LogData:
    rating: int | None
    review_text: str | None
    listened_on: date
    is_relisten: bool


@dataclass
class LogFormResult:
    data: LogData | None = None
    errors: dict[str, str] = field(default_factory=dict)
    values: LogFormValues = field(default_factory=LogFormValues)


@dataclass(frozen=True)
class LogView:
    id: int
    user_id: int
    username: str
    rating: int | None
    stars: str
    review_text: str | None
    listened_on: date
    is_relisten: bool
    is_owner: bool


def rating_to_stars(rating: int | None) -> str:
    """Render a 1–10 rating as half-stars (e.g. 7 → ★★★½)."""
    if rating is None:
        return ""
    full, half = divmod(rating, 2)
    return ("★" * full) + ("½" if half else "")


def average_to_stars(average: float | None) -> str:
    if average is None:
        return ""
    # Round to the nearest half-star step used by integer ratings.
    return rating_to_stars(max(1, min(10, int(round(average)))))


def default_listened_on(today: date | None = None) -> date:
    return today or date.today()


def empty_form_values(today: date | None = None) -> LogFormValues:
    return LogFormValues(listened_on=default_listened_on(today).isoformat())


def values_from_log(log: Log) -> LogFormValues:
    return LogFormValues(
        rating="" if log.rating is None else str(log.rating),
        review_text=log.review_text or "",
        listened_on=log.listened_on.isoformat(),
        is_relisten=log.is_relisten,
    )


def parse_log_form(
    *,
    rating: str | None,
    review_text: str | None,
    listened_on: str | None,
    is_relisten: bool,
    today: date | None = None,
) -> LogFormResult:
    today = default_listened_on(today)
    raw_rating = (rating or "").strip()
    raw_review = review_text if review_text is not None else ""
    raw_date = (listened_on or "").strip()
    values = LogFormValues(
        rating=raw_rating,
        review_text=raw_review,
        listened_on=raw_date or today.isoformat(),
        is_relisten=is_relisten,
    )
    errors: dict[str, str] = {}

    parsed_rating: int | None = None
    if raw_rating:
        try:
            parsed_rating = int(raw_rating)
        except ValueError:
            errors["rating"] = RATING_ERROR
        else:
            if parsed_rating < 1 or parsed_rating > 10:
                errors["rating"] = RATING_ERROR

    if len(raw_review) > REVIEW_MAX_LENGTH:
        errors["review_text"] = REVIEW_ERROR
    cleaned_review = raw_review.strip() or None

    parsed_date: date | None
    if not raw_date:
        parsed_date = today
        values.listened_on = today.isoformat()
    else:
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            errors["listened_on"] = DATE_INVALID_ERROR
            parsed_date = None
        else:
            if parsed_date > today:
                errors["listened_on"] = DATE_FUTURE_ERROR

    if errors or parsed_date is None:
        return LogFormResult(errors=errors, values=values)

    return LogFormResult(
        data=LogData(
            rating=parsed_rating,
            review_text=cleaned_review,
            listened_on=parsed_date,
            is_relisten=is_relisten,
        ),
        values=values,
    )


def create_log(session: Session, user: User, release_group: ReleaseGroup, data: LogData) -> Log:
    log = Log(
        user_id=user.id,
        release_group_id=release_group.id,
        rating=data.rating,
        review_text=data.review_text,
        listened_on=data.listened_on,
        is_relisten=data.is_relisten,
    )
    session.add(log)
    session.flush()
    return log


def update_log(session: Session, log: Log, data: LogData) -> Log:
    log.rating = data.rating
    log.review_text = data.review_text
    log.listened_on = data.listened_on
    log.is_relisten = data.is_relisten
    log.updated_at = datetime.now(timezone.utc)
    session.flush()
    return log


def delete_log(session: Session, log: Log) -> None:
    session.delete(log)
    session.flush()


def get_owned_log(session: Session, log_id: int, user_id: int) -> Log:
    """Return a log only when it exists and belongs to ``user_id``.

    Wrong owner and missing id both raise ``LogNotFound`` so callers always 404.
    """
    log = session.get(Log, log_id)
    if log is None or log.user_id != user_id:
        raise LogNotFound()
    return log


def recent_logs_for_album(
    session: Session, release_group_id: int, *, limit: int = RECENT_LOGS_LIMIT
) -> list[Log]:
    return list(
        session.scalars(
            select(Log)
            .where(Log.release_group_id == release_group_id)
            .options(joinedload(Log.user))
            .order_by(Log.listened_on.desc(), Log.created_at.desc())
            .limit(limit)
        ).unique()
    )


def average_rating_for_album(session: Session, release_group_id: int) -> float | None:
    value = session.scalar(
        select(func.avg(Log.rating)).where(
            Log.release_group_id == release_group_id,
            Log.rating.is_not(None),
        )
    )
    return float(value) if value is not None else None


def log_views(logs: list[Log], current_user: User | None) -> list[LogView]:
    owner_id = current_user.id if current_user is not None else None
    return [
        LogView(
            id=log.id,
            user_id=log.user_id,
            username=log.user.username,
            rating=log.rating,
            stars=rating_to_stars(log.rating),
            review_text=log.review_text,
            listened_on=log.listened_on,
            is_relisten=log.is_relisten,
            is_owner=owner_id is not None and log.user_id == owner_id,
        )
        for log in logs
    ]


def album_logging_context(
    session: Session,
    release_group: ReleaseGroup,
    current_user: User | None,
    *,
    today: date | None = None,
    form_values: LogFormValues | None = None,
    form_errors: dict[str, str] | None = None,
    editing_log: Log | None = None,
) -> dict:
    avg = average_rating_for_album(session, release_group.id)
    logs = recent_logs_for_album(session, release_group.id)
    return {
        "release_group_mbid": release_group.mbid,
        "average_rating": avg,
        "average_stars": average_to_stars(avg),
        "recent_logs": log_views(logs, current_user),
        "form_values": form_values or empty_form_values(today),
        "form_errors": form_errors or {},
        "editing_log_id": editing_log.id if editing_log is not None else None,
        "rating_choices": [
            (n, rating_to_stars(n) or "—", f"{n / 2:g}") for n in range(1, 11)
        ],
    }
