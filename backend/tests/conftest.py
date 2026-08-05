import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.memory.database as database_module
from app.main import app
from app.memory.models import Base

AUTH_HEADERS = {"Authorization": "Bearer dev-local-token"}


@pytest.fixture()
def client():
    """Fresh in-memory SQLite per test so tests never share state."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db_session():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[database_module.get_db_session] = override_get_db_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
