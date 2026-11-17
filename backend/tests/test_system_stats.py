import pytest

from tests.conftest import AUTH_HEADERS


@pytest.mark.parametrize("present", [False, True])
def test_gpu_contract_with_controlled_hardware(client, monkeypatch, present):
    from app.api import system
    from app.models.schemas import GpuStats
    gpu = GpuStats(name="Test GPU", load_percent=5, memory_used_mb=12, memory_total_mb=256) if present else None
    monkeypatch.setattr(system, "_collect_gpu_stats", lambda: gpu)
    response = client.get("/v1/system/stats", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["gpu"] == (gpu.model_dump() if present else None)


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

    # Machine-dependent by nature: `gpu` is None without an NVIDIA
    # driver and populated with one. Asserting either outcome makes the
    # suite fail on half the machines it runs on (it asserted None, so it
    # failed on the actual RTX 4070 target this app ships for). Assert the
    # contract instead — absent, or present and well-formed.
    gpu = body["gpu"]
    if gpu is not None:
        assert isinstance(gpu["name"], str) and gpu["name"]
        for numeric in ("load_percent", "memory_used_mb", "memory_total_mb"):
            assert isinstance(gpu[numeric], (int, float))
        assert gpu["memory_total_mb"] > 0
