import subprocess

import app.commands as commands
from tests.conftest import AUTH_HEADERS


def _fake_success(argv):
    return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")


def _fake_failure(argv):
    return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="boom")


def test_unknown_command_id_rejected(client):
    response = client.post("/commands/rm_rf_everything", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_allowlisted_command_runs_via_fixed_argv(client, monkeypatch):
    captured = {}

    def spy(argv):
        captured["argv"] = argv
        return _fake_success(argv)

    monkeypatch.setattr(commands, "run_subprocess", spy)

    response = client.post("/commands/launch_vscode", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["success"] is True
    # The argv actually invoked must exactly match the allowlisted definition —
    # nothing from the request path/body is ever interpolated into it.
    assert captured["argv"] == commands.COMMAND_DEFINITIONS["launch_vscode"].argv


def test_command_failure_surfaces_as_500(client, monkeypatch):
    monkeypatch.setattr(commands, "run_subprocess", _fake_failure)
    response = client.post("/commands/lock_computer", headers=AUTH_HEADERS)
    assert response.status_code == 500


def test_no_endpoint_accepts_arbitrary_shell_strings(client, monkeypatch):
    """There is no request field anywhere that becomes shell input — command
    selection is only ever by allowlisted ID in the URL path."""
    monkeypatch.setattr(commands, "run_subprocess", _fake_success)
    response = client.post(
        "/commands/launch_vscode",
        json={"raw_command": "rm -rf / --no-preserve-root"},
        headers=AUTH_HEADERS,
    )
    # The body is ignored entirely; only the fixed argv for launch_vscode runs.
    assert response.status_code == 200
