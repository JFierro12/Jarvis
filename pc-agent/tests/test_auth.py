from tests.conftest import AUTH_HEADERS


def test_status_requires_token(client):
    response = client.get("/status")
    assert response.status_code == 401


def test_status_rejects_wrong_token(client):
    response = client.get("/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_status_accepts_correct_token(client):
    response = client.get("/status", headers=AUTH_HEADERS)
    assert response.status_code == 200
