from fastapi import Request

USER_ID_KEY = "user_id"


def start_session(request: Request, user_id: int) -> None:
    """Replace any prior session contents (also rotates the CSRF token)."""
    request.session.clear()
    request.session[USER_ID_KEY] = user_id


def end_session(request: Request) -> None:
    request.session.clear()


def session_user_id(request: Request) -> int | None:
    value = request.session.get(USER_ID_KEY)
    return value if isinstance(value, int) else None


def safe_next_path(candidate: str | None, default: str = "/account") -> str:
    """Only allow same-site relative paths (prevents open redirects)."""
    if (
        candidate
        and candidate.startswith("/")
        and not candidate.startswith("//")
        and "\\" not in candidate
        and "\r" not in candidate
        and "\n" not in candidate
    ):
        return candidate
    return default
