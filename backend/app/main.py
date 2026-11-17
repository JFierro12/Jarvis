import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    assistant,
    dashboard,
    health,
    integrations,
    learning,
    location,
    memories,
    runtime,
    session,
    system,
    tools,
)
from app.core.config import get_settings
from app.core.process_lease import ProcessLease
from app.core.runtime import RuntimeStore
from app.memory.database import init_db, make_engine

logging.basicConfig(level=get_settings().log_level.upper())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = _app.dependency_overrides.get(get_settings, get_settings)()
    lease = ProcessLease(settings.runtime_database + ".lock")
    engine = make_engine(settings.database_url)
    _app.state.database_engine = engine
    _app.state.runtime = RuntimeStore(settings.runtime_database)
    _app.state.request_windows = {}
    from app.services.device_agent import LocalWindowsAgent
    _app.state.agents = getattr(_app.state, "agents", {})
    if settings.local_agent_enabled:
        _app.state.agents[settings.local_device_id] = LocalWindowsAgent()
    init_db(engine)
    try:
        yield
    finally:
        _app.state.runtime.close()
        del _app.state.runtime
        engine.dispose()
        lease.close()


app = FastAPI(title="JARVIS Backend", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def validate_transport(request, call_next):
    settings = app.dependency_overrides.get(get_settings, get_settings)()
    allowed_hosts = {v.strip() for v in settings.allowed_hosts.split(",")}
    allowed_origins = {v.strip() for v in settings.allowed_origins.split(",")}
    if request.url.hostname not in allowed_hosts:
        return JSONResponse({"detail": "Unrecognized host"}, status_code=400)
    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        return JSONResponse({"detail": "Unrecognized origin"}, status_code=403)
    windows = getattr(app.state, "request_windows", {})
    now = time.monotonic()
    peer = request.client.host if request.client else "unknown"
    for key in list(windows):
        while windows[key] and windows[key][0] < now - 60:
            windows[key].popleft()
        if not windows[key]:
            del windows[key]
    if peer not in windows and len(windows) >= 1024:
        return JSONResponse({"detail": "Server busy"}, status_code=429)
    window = windows.setdefault(peer, deque())
    if len(window) >= 240:
        return JSONResponse({"detail": "Request limit reached"}, status_code=429)
    window.append(now)
    if settings.live_mode and request.url.scheme != "https" and request.url.hostname not in {"127.0.0.1", "localhost"}:
        return JSONResponse({"detail": "Remote sessions require HTTPS"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; media-src 'self' blob:; connect-src 'self' https://api.open-meteo.com "
        "https://geocoding-api.open-meteo.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


@app.middleware("http")
async def no_store_dashboard_assets(request, call_next):
    # This dashboard is actively iterated on locally — a stale cached copy
    # of app.js/style.css/index.html silently running old logic after a
    # plain reload has caused real confusion (a feature looking broken when
    # it's actually just not loaded yet). A dev-mode dashboard should always
    # reflect what's on disk on the next reload.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(health.router)
app.include_router(session.router)
app.include_router(assistant.router)
app.include_router(memories.router)
app.include_router(tools.router)
app.include_router(runtime.router)
app.include_router(integrations.router)
app.include_router(system.router)
app.include_router(location.router)
app.include_router(learning.router)
# Dashboard last: it claims "/", and must never shadow any API route
# registered above it.
app.include_router(dashboard.router)
app.mount(
    "/assets",
    StaticFiles(directory=str(Path(__file__).parent / "static" / "assets")),
    name="dashboard-assets",
)
