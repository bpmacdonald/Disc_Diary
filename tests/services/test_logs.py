from datetime import date, timedelta

import pytest

from app.services.logs import (
    DATE_FUTURE_ERROR,
    RATING_ERROR,
    REVIEW_ERROR,
    REVIEW_MAX_LENGTH,
    average_to_stars,
    parse_log_form,
    rating_to_stars,
)


def test_rating_to_stars() -> None:
    assert rating_to_stars(None) == ""
    assert rating_to_stars(1) == "½"
    assert rating_to_stars(2) == "★"
    assert rating_to_stars(7) == "★★★½"
    assert rating_to_stars(10) == "★★★★★"


def test_average_to_stars_rounds_to_half_steps() -> None:
    assert average_to_stars(7.4) == "★★★½"
    assert average_to_stars(None) == ""


def test_parse_log_form_defaults_and_success() -> None:
    today = date(2026, 10, 10)
    result = parse_log_form(
        rating="7",
        review_text="  Great  ",
        listened_on="",
        is_relisten=True,
        today=today,
    )
    assert result.data is not None
    assert result.data.listened_on == today  # empty date defaults to today

    result = parse_log_form(
        rating="7",
        review_text="  Great  ",
        listened_on="2026-10-09",
        is_relisten=True,
        today=today,
    )
    assert result.data is not None
    assert result.data.rating == 7
    assert result.data.review_text == "Great"
    assert result.data.listened_on == date(2026, 10, 9)
    assert result.data.is_relisten is True


def test_parse_rejects_future_date_and_bad_rating() -> None:
    today = date(2026, 10, 10)
    result = parse_log_form(
        rating="11",
        review_text="",
        listened_on=(today + timedelta(days=1)).isoformat(),
        is_relisten=False,
        today=today,
    )
    assert result.errors["rating"] == RATING_ERROR
    assert result.errors["listened_on"] == DATE_FUTURE_ERROR


def test_parse_rejects_long_review() -> None:
    result = parse_log_form(
        rating="",
        review_text="x" * (REVIEW_MAX_LENGTH + 1),
        listened_on="2026-10-10",
        is_relisten=False,
        today=date(2026, 10, 10),
    )
    assert result.errors["review_text"] == REVIEW_ERROR
