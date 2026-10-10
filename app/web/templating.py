from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.security.csrf import get_csrf_token
from app.services.catalog import PLACEHOLDER_COVER_PATH

BASE_DIR = Path(__file__).resolve().parents[2]
# Autoescaping is on for .html templates.
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> Response:
    full_context: dict[str, Any] = {
        "csrf_token": get_csrf_token(request),
        "current_user": None,
        "placeholder_cover": PLACEHOLDER_COVER_PATH,
        "q": "",
    }
    full_context.update(context or {})
    return templates.TemplateResponse(
        request, name, full_context, status_code=status_code
    )
