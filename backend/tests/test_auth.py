from tests.conftest import AUTH_HEADERS


def test_missing_token_rejected(client):
    response = client.post("/v1/session", json={"device_id": "abc", "client_version": "1.0"})
    assert response.status_code == 401


def test_invalid_token_rejected(client):
    response = client.post(
        "/v1/session",
        json={"device_id": "abc", "client_version": "1.0"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_valid_token_accepted(client):
    response = client.post(
        "/v1/session",
        json={"device_id": "abc", "client_version": "1.0"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "session_id" in response.json()
