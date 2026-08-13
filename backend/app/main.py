import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import assistant, health, integrations, memories, session, tools
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
