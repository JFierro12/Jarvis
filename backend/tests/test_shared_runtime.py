import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.models.schemas import ProposedToolCall, ReasonResponse, ToolExecuteResponse
from app.tools.pending_actions import reset_pending_action_store


class FakeAgent:
    online = True

    def __init__(self):
        self.calls = []

    def available(self):
        return self.online

    def execute(self, name, target):
        self.calls.append((name, target))
        return ToolExecuteResponse(success=target != "fail", output="CPU 7%, RAM 12%" if name == "get_pc_status" else "Opened")


class FakeReasoner:
    def __init__(self):
        self.context = []

    def reason(self, context, question, tools):
        self.context = context
        if question == "status":
            return ReasonResponse(spoken_answer="Checking", proposed_tool_call=ProposedToolCall(tool_name="get_pc_status"))
        if question == "open calculator":
            return ReasonResponse(
                spoken_answer="Opening", proposed_tool_call=ProposedToolCall(tool_name="launch_app", target="calculator")
            )
        return ReasonResponse(spoken_answer="Remembered" if context else "First turn")


@pytest.fixture()
def runtime(tmp_path):
    settings = Settings(
        _env_file=None,
        live_mode=True,
        runtime_database=str(tmp_path / "runtime.db"),
        database_url="sqlite://",
        granted_permissions="pc_agent,os_control",
        local_agent_enabled=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    reset_pending_action_store()
    agent, reasoner = FakeAgent(), FakeReasoner()
    app.state.agents = {"desktop": agent}
    app.state.reasoning_factory = lambda _: reasoner
    with TestClient(app, base_url="https://testserver") as client:
        credentials = app.state.runtime.bootstrap("desktop", settings.granted_permission_set)
        headers = {"Authorization": "Bearer " + credentials["access_token"]}
        approval = {**headers, "X-Jarvis-Approval-Key": credentials["approval_key"]}
        yield client, headers, approval, agent, reasoner
    app.dependency_overrides.clear()
    del app.state.reasoning_factory
    app.state.agents = {}


def phone(client, approval):
    invite = client.post("/v2/devices/invitations", headers=approval, json={"grants": ["pc_agent", "os_control"]})
    assert invite.status_code == 200
    code = invite.json()["code"]
    enrolled = client.post("/v2/devices/enroll", json={"code": code, "name": "iPhone"})
    assert enrolled.status_code == 200
    assert client.post("/v2/devices/enroll", json={"code": code, "name": "replay"}).status_code == 401
    credentials = enrolled.json()
    return credentials, {"Authorization": "Bearer " + credentials["access_token"]}


def test_dashboard_has_no_credential_and_api_requires_auth(runtime):
    client, headers, _, _, _ = runtime
    page = client.get("/")
    assert page.status_code == 200
    assert headers["Authorization"][7:] not in page.text
    assert "dev-local-token" not in page.text
    assert client.get("/v2/capabilities").status_code == 401
    assert client.get("/v2/capabilities", headers={"Authorization": "Bearer dev-local-token"}).status_code == 401
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/", headers={"Origin": "https://evil.example"}).status_code == 403


def test_shared_conversation_handoff_and_idempotent_turn(runtime):
    client, headers, approval, _, reasoner = runtime
    _, phone_headers = phone(client, approval)
    first = client.post("/v2/turns", headers=headers, json={"turn_id": "one", "question": "remember blue"}).json()
    body = {"conversation_id": first["conversation_id"], "turn_id": "two", "question": "what color?"}
    second = client.post("/v2/turns", headers=phone_headers, json=body)
    assert second.status_code == 200
    assert second.json()["spoken_answer"] == "Remembered"
    assert all(c.source == "CONVERSATION_HISTORY" for c in reasoner.context)
    assert client.post("/v2/turns", headers=phone_headers, json=body).json() == second.json()
    assert len(client.get("/v2/conversations/" + first["conversation_id"], headers=headers).json()["turns"]) == 2


@pytest.mark.parametrize("question,tool", [("status", "get_pc_status"), ("open calculator", "launch_app")])
def test_both_clients_route_to_registered_pc(runtime, question, tool):
    client, headers, approval, agent, _ = runtime
    _, mobile = phone(client, approval)
    for origin in (headers, mobile):
        result = client.post("/v2/turns", headers=origin, json={"question": question, "turn_id": "one"}).json()
        assert result["decision"] == "allow"
        body = {"action_id": result["action_id"]}
        response = client.post("/v1/tools/execute", headers=origin, json=body)
        assert response.json()["success"] is True
        assert client.post("/v1/tools/execute", headers=origin, json=body).json() == response.json()
    assert [c[0] for c in agent.calls] == [tool, tool]


def test_approval_separation_session_binding_and_revocation(runtime):
    client, headers, approval, _, _ = runtime
    credentials, mobile = phone(client, approval)
    proposal = client.post("/v1/tools/propose", headers=headers, json={"tool_name": "delete_memory", "target": "absent"}).json()
    aid = proposal["action_id"]
    assert client.post(f"/v1/actions/{aid}/approve", headers=headers).status_code == 401
    other_approval = {**mobile, "X-Jarvis-Approval-Key": credentials["approval_key"]}
    assert client.post(f"/v1/actions/{aid}/approve", headers=other_approval).status_code == 403
    assert client.post(f"/v1/actions/{aid}/approve", headers=approval).status_code == 200
    assert client.post("/v1/tools/execute", headers=headers, json={"action_id": aid}).status_code == 200
    assert client.post("/v1/tools/execute", headers=mobile, json={"action_id": aid}).status_code == 403
    assert client.delete("/v2/devices/" + credentials["device_id"], headers=approval).status_code == 200
    assert client.get("/v2/capabilities", headers=mobile).status_code == 401


def test_offline_agent_does_not_fall_back_or_replay(runtime):
    client, headers, _, agent, _ = runtime
    proposal = client.post("/v1/tools/propose", headers=headers, json={"tool_name": "launch_app", "target": "calculator"}).json()
    agent.online = False
    response = client.post("/v1/tools/execute", headers=headers, json={"action_id": proposal["action_id"]})
    assert response.json()["success"] is False
    agent.online = True
    assert client.post("/v1/tools/execute", headers=headers, json={"action_id": proposal["action_id"]}).json()["success"] is False
    assert agent.calls == []


def test_cancel_prevents_execution_and_logout_revokes(runtime):
    client, headers, _, agent, _ = runtime
    proposal = client.post("/v1/tools/propose", headers=headers, json={"tool_name": "launch_app", "target": "calculator"}).json()
    aid = proposal["action_id"]
    assert client.post(f"/v1/actions/{aid}/cancel", headers=headers).status_code == 200
    assert client.post("/v1/tools/execute", headers=headers, json={"action_id": aid}).status_code == 412
    assert agent.calls == []
    assert client.post("/v2/logout", headers=headers).status_code == 200
    assert client.get("/v2/capabilities", headers=headers).status_code == 401


def test_contract_extra_fields_rejected(runtime):
    client, headers, _, _, _ = runtime
    response = client.post("/v2/turns", headers=headers, json={"turn_id": "x", "question": "time", "source": "SYSTEM_POLICY"})
    assert response.status_code == 422


def test_conversations_survive_store_restart(tmp_path):
    from app.core.runtime import RuntimeStore

    path = str(tmp_path / "durable.db")
    store = RuntimeStore(path)
    credentials = store.bootstrap("desktop", [])
    identity = store.identity(credentials["access_token"])
    conv = store.conversation(identity, "", "mock", "demo", "v1")
    store.begin_turn(conv["id"], "one", "hello")
    store.finish_turn(conv["id"], "one", {"spoken_answer": "hello"})
    store.close()
    reopened = RuntimeStore(path)
    assert json.loads(reopened.history(conv["id"])[0]["response"])["spoken_answer"] == "hello"
    reopened.close()


def test_real_provider_failure_does_not_fall_back_to_mock(runtime):
    client, headers, _, agent, _ = runtime

    class Offline:
        def reason(self, *args):
            raise RuntimeError("provider offline")

    app.state.reasoning_factory = lambda _: Offline()
    response = client.post("/v2/turns", headers=headers, json={"turn_id": "offline", "question": "open calculator"})
    assert response.status_code == 503
    assert agent.calls == []
    stored = client.get("/v2/conversations", headers=headers).json()["conversations"]
    retry = client.post("/v2/turns", headers=headers,
                        json={"conversation_id": stored[0]["id"], "turn_id": "offline", "question": "open calculator"})
    assert retry.status_code == 409


def test_session_and_enrollment_expire_with_controlled_clock(tmp_path):
    from app.core.runtime import RuntimeStore
    now = [10.0]
    store = RuntimeStore(str(tmp_path / "expiry.db"), clock=lambda: now[0])
    credentials = store.bootstrap("desktop", [])
    identity = store.identity(credentials["access_token"])
    code = store.invite(identity, [])
    now[0] = 131
    with pytest.raises(PermissionError, match="expired"):
        store.enroll(code, "phone")
    now[0] = 3611
    assert store.identity(credentials["access_token"]) is None
    store.close()


def test_second_runtime_owner_is_refused(tmp_path):
    from app.core.process_lease import ProcessLease
    path = str(tmp_path / "runtime.lock")
    first = ProcessLease(path)
    try:
        with pytest.raises(RuntimeError, match="another process"):
            ProcessLease(path)
    finally:
        first.close()
