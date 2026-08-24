from tests.conftest import AUTH_HEADERS


def test_system_stats_requires_auth(client):
    response = client.get("/v1/system/stats")
    assert response.status_code == 401


def test_system_stats_returns_expected_shape(client):
    response = client.get("/v1/system/stats", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    for key in (
        "cpu_percent",
        "ram_percent",
        "ram_used_gb",
        "ram_total_gb",
        "disk_percent",
        "disk_used_gb",
        "disk_total_gb",
        "uptime_seconds",
        "gpu",
    ):
        assert key in body
    # No NVIDIA GPU/driver on the machine running this test suite -> None,
    # rather than an error. Real GPU values are only verified on the actual
    # Windows/RTX 4070 target machine (see desktop/README.md).
    assert body["gpu"] is None
