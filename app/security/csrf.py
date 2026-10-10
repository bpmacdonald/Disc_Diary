"""Synchronizer-token CSRF protection backed by the session."""

import secrets

from fastapi import HTTPException, Request

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"  # for HTMX: hx-headers='{"X-CSRF-Token": "..."}'


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def _submitted_token(request: Request) -> str:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    form = await request.form()
    value = form.get(CSRF_FORM_FIELD)
    return value if isinstance(value, str) else ""


async def verify_csrf(request: Request) -> None:
    """Dependency for every state-changing (POST/PUT/PATCH/DELETE) route."""
    expected = request.session.get(CSRF_SESSION_KEY)
    submitted = await _submitted_token(request)
    if not isinstance(expected, str) or not expected or not submitted:
        raise HTTPException(status_code=403, detail="Missing CSRF token")
    if not secrets.compare_digest(submitted.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
