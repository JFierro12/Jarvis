import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    """Short-lived bearer token check (spec 25). Never logs the raw token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    runtime = getattr(request.app.state, "runtime", None)
    if runtime and runtime.identity(credentials.credentials):
        return credentials.credentials
    if settings.live_mode or credentials.credentials not in settings.auth_token_set:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return credentials.credentials


def require_approval_credential(
    request: Request,
    token: str = Depends(require_bearer_token),
    approval_key: Optional[str] = Header(default=None, alias="X-Jarvis-Approval-Key"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Gate for the human-approval path.

    Deliberately a *different* credential from the API bearer token. An
    approval endpoint sitting behind the same unrestricted token would add
    a step without adding a boundary: whatever can call /v1/tools/propose
    could approve its own proposal. Requiring a separate secret means the
    reasoning provider and the automatic tool executor — which hold only
    the API token — cannot approve anything.

    What this does prove: the caller holds the trusted UI's credential.
    What it cannot prove: that a human approved. A compromised trusted UI
    can approve by itself, and this process has no independent channel to
    detect that.
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime and runtime.identity(token):
        if approval_key and runtime.approval_valid(token, approval_key):
            return approval_key
        raise HTTPException(status_code=401, detail="Invalid session approval credential")
    expected = settings.approval_key.strip()
    if not expected:
        # Fail closed: without a configured approval credential there is
        # no approval path at all, rather than an open one.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No approval credential is configured; approvals are unavailable",
        )
    if not approval_key or not secrets.compare_digest(approval_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid approval credential")
    return approval_key


def caller_identity(request: Request, token: str, settings: Settings):
    from app.core.runtime import Identity, digest

    runtime = getattr(request.app.state, "runtime", None)
    identity = runtime.identity(token) if runtime else None
    if identity:
        return identity
    if not settings.live_mode and token in settings.auth_token_set:
        # Compatibility identity is confined to explicit demo/test operation.
        return Identity(digest(token)[:32], "legacy", "desktop", frozenset(settings.granted_permission_set))
    raise HTTPException(status_code=401, detail="Session expired or revoked")
