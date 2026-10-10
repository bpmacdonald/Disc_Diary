from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.security.csrf import get_csrf_token

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
    full_context = {"csrf_token": get_csrf_token(request), **(context or {})}
    return templates.TemplateResponse(
        request, name, full_context, status_code=status_code
    )
