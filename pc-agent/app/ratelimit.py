import time
from collections import deque
from threading import Lock
from typing import Deque, Dict

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


class RateLimiter:
    """Simple fixed-window-ish limiter: N requests per rolling 60s, keyed by
    client host. Good enough for a single-user local agent; not intended to
    withstand a distributed attack (see docs/THREAT_MODEL.md)."""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, limit_per_minute: int) -> bool:
        now = time.monotonic()
        with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= limit_per_minute:
                return False
            window.append(now)
            return True


_limiter = RateLimiter()


def enforce_rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    client_key = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_key, settings.rate_limit_per_minute):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
