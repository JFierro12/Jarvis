import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import assistant, dashboard, health, integrations, memories, session, system, tools
from app.core.config import get_settings
from app.memory.database import init_db

logging.basicConfig(level=get_settings().log_level.upper())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="JARVIS Backend", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(session.router)
app.include_router(assistant.router)
app.include_router(memories.router)
app.include_router(tools.router)
app.include_router(integrations.router)
app.include_router(system.router)
# Dashboard last: it claims "/", and must never shadow any API route
# registered above it.
app.include_router(dashboard.router)
app.mount(
    "/assets",
    StaticFiles(directory=str(Path(__file__).parent / "static" / "assets")),
    name="dashboard-assets",
)
