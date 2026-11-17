from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.core.config import Settings, get_settings

router = APIRouter()

_INDEX_PATH = Path(__file__).parent.parent / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """Public shell with no credentials. Only the native host can bootstrap it."""
    html = _INDEX_PATH.read_text(encoding="utf-8")
    provider = "local" if settings.stt_provider == "local" else "mock"
    html = html.replace("{{STT_PROVIDER}}", provider)
    return HTMLResponse(html)
