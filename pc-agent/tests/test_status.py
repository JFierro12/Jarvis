from tests.conftest import AUTH_HEADERS


def test_status_returns_real_metrics(client):
    response = client.get("/status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["host_name"]
    assert 0.0 <= body["cpu_utilization"] <= 100.0
    assert 0.0 <= body["memory_utilization"] <= 100.0
    assert body["uptime_seconds"] > 0
