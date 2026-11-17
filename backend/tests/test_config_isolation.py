"""Proof that the suite cannot be steered by real configuration.

Verified against hostile values and a disposable fake dotenv file — never
by editing the developer's own .env.
"""

import os
import subprocess
import sys
import textwrap


def test_suite_is_not_bound_to_the_real_database():
    """The engine built at import time must point at the temp database,
    not ./jarvis.db. This was the actual defect: request-level DB
    overrides hid it, because `lifespan`'s init_db() uses this engine."""
    import app.memory.database as database_module

    assert not hasattr(database_module, "_engine"), "Import must not create an engine from real configuration"


def test_settings_ignore_the_developers_dotenv_and_shell():
    from app.core.config import Settings

    settings = Settings()
    # Real .env sets these to live values; the suite must not see them.
    assert settings.reasoning_provider == "mock"
    assert settings.stt_provider == "mock"
    assert settings.tts_provider == "mock"
    assert settings.granted_permissions == ""
    assert "jarvis-tests-" in settings.allowed_folders


def test_hostile_environment_and_fake_dotenv_cannot_reach_settings(tmp_path):
    """Run a fresh interpreter with a poisoned environment and a planted
    dotenv, and confirm conftest's isolation wins in both cases."""
    poisoned_dotenv = tmp_path / "evil.env"
    poisoned_dotenv.write_text(
        "JARVIS_GRANTED_PERMISSIONS=os_control,file_access\n"
        "JARVIS_ALLOWED_FOLDERS=C:/\n"
        "JARVIS_APPROVAL_KEY=attacker-key\n",
        encoding="utf-8",
    )

    script = textwrap.dedent(
        """
        import tests.conftest  # installs isolation at import time
        from app.core.config import Settings
        s = Settings()
        assert s.granted_permissions == "", s.granted_permissions
        assert s.approval_key == "test-approval-key", s.approval_key
        assert s.allowed_folders != "C:/", s.allowed_folders
        assert s.reasoning_provider == "mock", s.reasoning_provider
        print("ISOLATED")
        """
    )

    env = dict(os.environ)
    env.update(
        {
            # Hostile process environment...
            "JARVIS_GRANTED_PERMISSIONS": "os_control,file_access,screen_capture",
            "JARVIS_APPROVAL_KEY": "attacker-key",
            "JARVIS_ALLOWED_FOLDERS": "C:/",
            "JARVIS_REASONING_PROVIDER": "anthropic",
            # ...and a hostile dotenv.
            "JARVIS_ENV_FILE": str(poisoned_dotenv),
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert "ISOLATED" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr[-800:]!r}"


def test_no_real_capability_is_granted_by_default(client):
    """Default fixture grants nothing, so no test can trip a real
    screen capture, file write, or process launch by accident."""
    from tests.conftest import AUTH_HEADERS

    for tool in ("take_screenshot", "launch_app", "write_file"):
        response = client.post(
            "/v1/tools/execute",
            json={"tool_name": tool, "target": "x"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 403, f"{tool} was not denied"
