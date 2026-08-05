from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


def require_pairing_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing pairing token")
    if credentials.credentials != settings.pairing_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid pairing token")
    return credentials.credentials
