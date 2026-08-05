from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.memory.models import Base


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


_engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def init_db(engine=None) -> None:
    Base.metadata.create_all(engine or _engine)


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
