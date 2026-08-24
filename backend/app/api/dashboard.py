from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.core.config import Settings, get_settings

router = APIRouter()

_INDEX_PATH = Path(__file__).parent.parent / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """Serves the desktop dashboard shell.

    The page itself isn't sensitive; every API call it makes still goes
    through `require_bearer_token` like any other client (see
    `app/core/security.py`). The token is injected server-side here — this
    is a single-user local desktop app sharing the same one-token auth model
    already used by the iOS client, not a new auth mechanism.
    """
    html = _INDEX_PATH.read_text()
    token = next(iter(settings.auth_token_set), "")
    return HTMLResponse(html.replace("{{TOKEN}}", token))
