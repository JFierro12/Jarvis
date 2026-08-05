from tests.conftest import AUTH_HEADERS


def test_read_only_tool_allowed(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "get_current_time", "granted_permissions": []},
        headers=AUTH_HEADERS,
    )
    assert response.json()["decision"] == "allow"


def test_unknown_tool_denied(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "rm_rf_everything", "granted_permissions": []},
        headers=AUTH_HEADERS,
    )
    body = response.json()
    assert body["decision"] == "deny"
    assert "Unknown tool" in body["reason"]


def test_missing_permission_denied(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "get_pc_status", "granted_permissions": []},
        headers=AUTH_HEADERS,
    )
    body = response.json()
    assert body["decision"] == "deny"
    assert "pc_agent" in body["reason"]


def test_granted_permission_allows_read_only_pc_status(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "get_pc_status", "granted_permissions": ["pc_agent"]},
        headers=AUTH_HEADERS,
    )
    assert response.json()["decision"] == "allow"


def test_destructive_tool_requires_confirmation(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "delete_all_memories", "granted_permissions": []},
        headers=AUTH_HEADERS,
    )
    body = response.json()
    assert body["decision"] == "require_confirmation"
    assert body["risk_level"] == "destructive"


def test_execute_without_confirmation_rejected(client):
    response = client.post(
        "/v1/tools/execute",
        json={"tool_name": "delete_all_memories", "confirmed": False},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412


def test_execute_unknown_tool_rejected(client):
    response = client.post(
        "/v1/tools/execute",
        json={"tool_name": "rm_rf_everything", "confirmed": True},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403
