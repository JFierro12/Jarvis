import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

AUTH_HEADERS = {"Authorization": "Bearer test-pairing-token"}


@pytest.fixture(autouse=True)
def _override_settings():
    get_settings.cache_clear()

    def _settings_override():
        from app.config import Settings

        return Settings(pairing_token="test-pairing-token", rate_limit_per_minute=1000)

    app.dependency_overrides[get_settings] = _settings_override
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def client():
    from app.ratelimit import _limiter

    _limiter._hits.clear()
    with TestClient(app) as test_client:
        yield test_client
