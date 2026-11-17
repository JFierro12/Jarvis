"""HTTP-level authorization: permissions, approval separation, execution.

Store-level properties (serialization, state machine, concurrency) live in
test_pending_actions.py. These cover the wire contract.

Each request is issued exactly once and its response reused — consuming an
approval is itself a state change, so calling twice to "check the status
and then read the body" would silently spend it.
"""

from tests.conftest import APPROVAL_HEADERS, AUTH_HEADERS

RISKY = {"tool_name": "delete_all_memories", "target": ""}


# --- Permissions are not the caller's to claim ---


def test_client_cannot_grant_itself_a_permission(client):
    response = client.post(
        "/v1/tools/execute",
        json={"tool_name": "delete_file", "target": "x.txt", "granted_permissions": ["file_access"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403
    assert "permission" in response.json()["detail"].lower()


def test_propose_ignores_client_supplied_permissions(client):
    response = client.post(
        "/v1/tools/propose",
        json={"tool_name": "launch_app", "target": "notepad", "granted_permissions": ["os_control"]},
        headers=AUTH_HEADERS,
    )
    assert response.json()["decision"] == "deny"


# --- Proposal alone authorizes nothing ---


def test_proposal_returns_an_id_not_a_credential(client):
    body = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()
    assert body["decision"] == "require_confirmation"
    assert body["action_id"]
    # The old shape handed back something immediately spendable.
    assert "confirmation_token" not in body


def test_api_caller_cannot_execute_its_own_unapproved_proposal(client):
    """The gap this lifecycle exists to close: propose and execute used to
    be back-to-back for anyone holding the API token."""
    action_id = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()["action_id"]
    response = client.post(
        "/v1/tools/execute", json={**RISKY, "action_id": action_id}, headers=AUTH_HEADERS
    )
    assert response.status_code == 412
    assert "not approved" in response.json()["detail"].lower()


def test_execute_without_any_action_id_is_refused_for_risky_tools(client):
    response = client.post("/v1/tools/execute", json=RISKY, headers=AUTH_HEADERS)
    assert response.status_code == 412
    assert "approval required" in response.json()["detail"].lower()


def test_legacy_confirmed_flag_is_inert(client):
    response = client.post(
        "/v1/tools/execute", json={**RISKY, "confirmed": True}, headers=AUTH_HEADERS
    )
    assert response.status_code == 412


# --- Approval requires a separate credential ---


def test_api_token_alone_cannot_approve(client):
    """An approval endpoint behind the same unrestricted token would add a
    step without adding a boundary."""
    action_id = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()["action_id"]
    response = client.post(f"/v1/actions/{action_id}/approve", headers=AUTH_HEADERS)
    assert response.status_code == 401


def test_wrong_approval_credential_is_refused(client):
    action_id = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()["action_id"]
    response = client.post(
        f"/v1/actions/{action_id}/approve",
        headers={**AUTH_HEADERS, "X-Jarvis-Approval-Key": "guessed"},
    )
    assert response.status_code == 401


def test_approved_action_executes_once_and_retry_does_not_repeat_it(client):
    action_id = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()["action_id"]
    assert client.post(f"/v1/actions/{action_id}/approve", headers=APPROVAL_HEADERS).status_code == 200

    first = client.post("/v1/tools/execute", json={**RISKY, "action_id": action_id}, headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert first.json()["execution_id"]

    # A retry after an uncertain outcome reports the recorded result
    # rather than performing the side effect again.
    second = client.post("/v1/tools/execute", json={**RISKY, "action_id": action_id}, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert second.json()["execution_id"] == first.json()["execution_id"]


def test_rejected_action_cannot_execute(client):
    action_id = client.post("/v1/tools/propose", json=RISKY, headers=AUTH_HEADERS).json()["action_id"]
    assert client.post(f"/v1/actions/{action_id}/reject", headers=APPROVAL_HEADERS).status_code == 200
    response = client.post("/v1/tools/execute", json={**RISKY, "action_id": action_id}, headers=AUTH_HEADERS)
    assert response.status_code == 412


def test_unknown_action_id_is_refused(client):
    response = client.post(
        "/v1/tools/execute", json={**RISKY, "action_id": "nope"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 412


# --- Execution uses the stored action, not resubmitted fields ---


def test_execution_ignores_client_fields_and_runs_the_approved_action(granted_client):
    """Approving a delete of one memory must not delete another, even if
    the execute call carries different fields."""
    created = granted_client.post(
        "/v1/memories",
        json={
            "type": "note",
            "title": "keep me",
            "original_user_text": "keep",
            "normalized_summary": "keep",
        },
        headers=AUTH_HEADERS,
    ).json()

    approved_id = granted_client.post(
        "/v1/tools/propose",
        json={"tool_name": "delete_memory", "target": "some-other-id"},
        headers=AUTH_HEADERS,
    ).json()["action_id"]
    assert granted_client.post(f"/v1/actions/{approved_id}/approve", headers=APPROVAL_HEADERS).status_code == 200

    # Execute, but try to swap the target to the real memory.
    granted_client.post(
        "/v1/tools/execute",
        json={"tool_name": "delete_memory", "target": created["id"], "action_id": approved_id},
        headers=AUTH_HEADERS,
    )

    # The memory that was never approved for deletion must still be there.
    found = granted_client.get(
        "/v1/memories/search", params={"q": "keep"}, headers=AUTH_HEADERS
    ).json()["results"]
    assert any(r["id"] == created["id"] for r in found), "swapped target must not have been acted on"
