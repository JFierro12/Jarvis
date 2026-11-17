from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.memory.models import Base


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def get_db_session(request: Request):
    db = sessionmaker(bind=request.app.state.database_engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
