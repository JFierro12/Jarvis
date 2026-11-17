"""Opt-in browser smoke test. Uses disposable databases and a fake reasoner.

No microphones, external providers, or PC actions. Requires installed Playwright
Chromium; never downloads a browser or model.
"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn
from app.core.config import Settings, get_settings
from app.main import app
from app.models.schemas import ProposedToolCall, ReasonResponse
from playwright.sync_api import sync_playwright


class Reasoner:
    def reason(self, context, question, tools):
        return ReasonResponse(spoken_answer="Delete the test memory?",
                              proposed_tool_call=ProposedToolCall(tool_name="delete_memory", target="absent-test-memory"))


def main():
    with tempfile.TemporaryDirectory(prefix="jarvis-ui-check-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        settings = Settings(_env_file=None, live_mode=True, local_agent_enabled=False,
                            runtime_database=str(Path(directory) / "runtime.db"),
                            database_url="sqlite:///" + str(Path(directory) / "memory.db").replace("\\", "/"),
                            allowed_hosts="127.0.0.1", allowed_origins=origin, granted_permissions="",
                            reasoning_provider="mock", vision_provider="mock", tts_provider="mock", stt_provider="mock")
        app.dependency_overrides[get_settings] = lambda: settings
        app.state.reasoning_factory = lambda _: Reasoner()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Test server failed to start")
                time.sleep(0.05)
            credentials = app.state.runtime.bootstrap("desktop", [])
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                page.add_init_script("speechSynthesis.speak = utterance => setTimeout(() => utterance.onend && utterance.onend(), 0)")
                page.goto(origin)
                page.evaluate("session => window.jarvisBootstrap(session)", credentials)
                page.locator("#composer-input").fill("delete the test memory")
                page.locator("#composer-send").click()
                page.get_by_role("button", name="Confirm", exact=True).click()
                page.get_by_text("already deleted", exact=True).wait_for()
                page.locator("#composer-input").fill("delete another test memory")
                page.locator("#composer-send").click()
                page.get_by_role("button", name="Cancel", exact=True).click()
                page.get_by_role("button", name="Cancel", exact=True).wait_for(state="detached")
                assert not errors, errors
                browser.close()
            print("PASS: desktop login, proposal, explicit confirmation, result rendering, cancellation")
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
