from app.config import Settings, get_settings
from app.main import app
from tests.conftest import AUTH_HEADERS


def test_rate_limit_enforced(client):
    def strict_settings():
        return Settings(pairing_token="test-pairing-token", rate_limit_per_minute=2)

    app.dependency_overrides[get_settings] = strict_settings
    try:
        first = client.get("/status", headers=AUTH_HEADERS)
        second = client.get("/status", headers=AUTH_HEADERS)
        third = client.get("/status", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
