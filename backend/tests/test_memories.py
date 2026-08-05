from tests.conftest import AUTH_HEADERS


def _create(client, title="keys", summary="Keys on the kitchen counter"):
    return client.post(
        "/v1/memories",
        json={
            "type": "object_location",
            "title": title,
            "original_user_text": f"remember where I put my {title}",
            "normalized_summary": summary,
        },
        headers=AUTH_HEADERS,
    )


def test_create_memory(client):
    response = _create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "keys"
    assert body["id"]


def test_search_finds_created_memory(client):
    _create(client)
    response = client.get("/v1/memories/search", params={"q": "keys"}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["title"] == "keys"


def test_search_no_match_returns_empty(client):
    _create(client)
    response = client.get("/v1/memories/search", params={"q": "wallet"}, headers=AUTH_HEADERS)
    assert response.json()["results"] == []


def test_delete_memory(client):
    created = _create(client).json()
    delete_response = client.delete(f"/v1/memories/{created['id']}", headers=AUTH_HEADERS)
    assert delete_response.status_code == 204

    search_response = client.get("/v1/memories/search", params={"q": "keys"}, headers=AUTH_HEADERS)
    assert search_response.json()["results"] == []


def test_delete_nonexistent_memory_returns_404(client):
    response = client.delete("/v1/memories/does-not-exist", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_delete_is_idempotent_via_tool_execute(client):
    created = _create(client).json()
    first = client.post(
        "/v1/tools/execute",
        json={"tool_name": "delete_memory", "target": created["id"], "confirmed": True},
        headers=AUTH_HEADERS,
    )
    second = client.post(
        "/v1/tools/execute",
        json={"tool_name": "delete_memory", "target": created["id"], "confirmed": True},
        headers=AUTH_HEADERS,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["output"] == "deleted"
    assert second.json()["output"] == "already deleted"
