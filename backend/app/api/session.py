import secrets

from fastapi import APIRouter, Depends

from app.core.security import require_bearer_token
from app.models.schemas import SessionRequest, SessionResponse

router = APIRouter()


@router.post("/v1/session", response_model=SessionResponse)
def create_session(payload: SessionRequest, _token: str = Depends(require_bearer_token)) -> SessionResponse:
    return SessionResponse(session_id=secrets.token_urlsafe(16), expires_in_seconds=3600)
