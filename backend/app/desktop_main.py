"""Entry point for the packaged Windows desktop app (see desktop/README.md).

Not used by the normal `uvicorn app.main:app` dev/deploy path — this wraps
the same ASGI app in a native window + system tray so it can run 24/7 as a
background app rather than a terminal process. PyInstaller's entry point is
this module (see desktop/jarvis.spec).
"""

import ipaddress
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

from app.core import desktop_logging

PORT = 8756

# How long the page may make no request at all before the host treats its
# renderer as dead. The dashboard polls /v1/system/stats every 2 seconds, so
# any silence here is orders of magnitude past normal; the margin is for
# Chromium throttling timers in a window that is hidden to the tray.
PAGE_SILENCE_LIMIT_SECONDS = 300
WATCHDOG_INTERVAL_SECONDS = 30
# Fraction of the session lifetime at which to renew. Early enough that a
# missed tick is not fatal, late enough not to churn sessions.
SESSION_RENEWAL_FRACTION = 0.75

# Frozen (PyInstaller) Playwright resolves its default browser search path
# relative to its own bundled driver location, not the normal per-user
# cache — so a browser installed the ordinary way (`playwright install
# chromium`, landing in %LOCALAPPDATA%\ms-playwright) is invisible to the
# packaged app unless pointed at explicitly. Must be set before
# browser_automation.py's lazy `from playwright.sync_api import
# sync_playwright` runs anywhere, so it happens here at the very top.
if sys.platform == "win32" and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local_appdata) / "ms-playwright")

# console=False (jarvis.spec) means there is no stdout/stderr to see at all
# for this windowed app — without a file handler, every error (including
# unhandled exceptions in request handlers) is silently lost, making the
# packaged app nearly undebuggable. Configured before importing app.main so
# it's in place for that module's own logging.basicConfig() call too.
#
# The path comes from app.core.desktop_logging rather than being derived from
# sys.executable: that put the log inside .venv/Scripts when run from source,
# and inside a typically-unwritable Program Files directory when installed,
# where opening it raised during import and stopped the app from starting at
# all. See that module for the full reasoning.
_LOG_PATH = desktop_logging.log_path()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=desktop_logging.build_handlers(_LOG_PATH),
)

from app.main import app  # noqa: E402 — must follow the logging setup above


def _build_tray_icon():
    from PIL import Image

    # Bundled via jarvis.spec's `datas` — sys._MEIPASS is PyInstaller's
    # runtime extraction root (present in both --onedir and --onefile
    # builds), not something this module has any other reason to reference.
    #
    # The non-frozen branch matters: running this module from source is the
    # only way to drive the dashboard against uncommitted backend changes,
    # and the previous fallback (`Path(sys.executable).parent`) resolved to
    # the venv's Scripts/ directory, so a source launch always died with
    # FileNotFoundError on .../venv/Scripts/assets/icon.png. Fall back to the
    # icon the dashboard itself serves at /assets/icon.png instead, which is
    # in the tree next to this file and needs no build step.
    if hasattr(sys, "_MEIPASS"):
        icon_path = Path(sys._MEIPASS) / "assets" / "icon.png"
    else:
        icon_path = Path(__file__).parent / "static" / "assets" / "icon.png"
    return Image.open(icon_path)


def _set_autostart(enabled: bool) -> None:
    """Toggle launch-at-login via the current user's Run key. Windows-only,
    and only ever called from an explicit tray-menu action — never silently
    enabled on first run.
    """
    if sys.platform != "win32":
        return
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "JARVIS", 0, winreg.REG_SZ, sys.executable)
        else:
            try:
                winreg.DeleteValue(key, "JARVIS")
            except FileNotFoundError:
                pass


def _patch_webview2_mic_permission() -> None:
    """pywebview's Windows backend (webview/platforms/edgechromium.py) never
    wires up CoreWebView2.PermissionRequested — that event handler only
    exists for the Qt backend (webview/platforms/qt.py). WebView2's
    undocumented default for an unhandled permission request is Deny, which
    silently breaks wake word / push-to-talk (getUserMedia() never
    succeeds) with no error beyond a bare "not-allowed" in JS. This webview
    only ever loads our own localhost dashboard — never an arbitrary site —
    so auto-granting the microphone here carries none of the "random
    website wants your mic" risk it would in a general-purpose browser.
    """
    try:
        # Import order matters, and this order is load-bearing:
        # `Microsoft.Web.WebView2.Core` is a .NET assembly that only becomes
        # importable once edgechromium.py's own clr.AddReference() calls have
        # run, and those run as a side effect of importing edgechromium. So
        # EdgeChrome must come FIRST. With the two lines the other way round
        # this raised `ModuleNotFoundError: No module named 'Microsoft'` on
        # every launch, which the bare `from None` below then reported only as
        # "Cannot establish trusted WebView2 navigation boundary" -- a
        # security-sounding message for what was really an import ordering bug.
        # noqa on the block: isort would sort `Microsoft` above `webview`,
        # which is precisely the broken order described above. Do not let it.
        from webview.platforms.edgechromium import EdgeChrome  # noqa: I001
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState  # noqa: I001
    except Exception as exc:
        logging.getLogger("jarvis.desktop").exception("Could not patch WebView2 mic permission handling")
        # Keep failing closed -- without the permission handler the mic is
        # silently denied -- but name the cause, so the next person does not
        # have to reproduce the import by hand to find out what broke.
        raise RuntimeError(
            f"Cannot establish trusted WebView2 navigation boundary: {type(exc).__name__}: {exc}"
        ) from exc

    original_on_webview_ready = EdgeChrome.on_webview_ready
    original_navigation = EdgeChrome.on_navigation_start
    trusted_url = f"http://127.0.0.1:{PORT}/"

    def guarded_navigation(self, sender, args):
        if str(args.get_Uri()) != trusted_url:
            args.set_Cancel(True)
            return
        original_navigation(self, sender, args)

    def block_new_window(self, sender, args):
        args.set_Handled(True)

    EdgeChrome.on_navigation_start = guarded_navigation
    EdgeChrome.on_new_window_request = block_new_window

    def patched_on_webview_ready(self, sender, args):
        original_on_webview_ready(self, sender, args)

        def on_permission_requested(_sender, e):
            trusted = str(e.get_Uri()).rstrip("/") == trusted_url.rstrip("/")
            if trusted and e.get_PermissionKind() == CoreWebView2PermissionKind.Microphone:
                e.set_State(CoreWebView2PermissionState.Allow)
            else:
                e.set_State(CoreWebView2PermissionState.Deny)

        try:
            sender.CoreWebView2.PermissionRequested += on_permission_requested
        except Exception:
            logging.getLogger("jarvis.desktop").exception("Failed to attach WebView2 PermissionRequested handler")

    EdgeChrome.on_webview_ready = patched_on_webview_ready


def page_watchdog_action(now, last_response, renew_at, has_session,
                        reinject_at=None, silence_limit=PAGE_SILENCE_LIMIT_SECONDS):
    """What the watchdog should do this tick: "reload", "renew", or None.

    Kept pure and separate from the pywebview calls because the policy is
    the part that can be wrong. Two failures motivate it, both of which
    only appear after the window has been open for hours:

    The session the host injects lasts an hour and the page has no way to
    ask for another. Credentials reach it only through run_js, by design,
    so there is no refresh endpoint to call. Left alone, every request 401s
    after an hour and the dashboard freezes with no explanation.

    Separately, a WebView2 render process can die while the browser process
    survives. The window then paints white indefinitely: pywebview 6.2.1
    surfaces no ProcessFailed event, so nothing notices and nothing
    reloads. Observed in the wild with the browser, GPU and utility
    processes all still alive and no renderer among them.

    Silence is not on its own enough to act on. After Disconnect the page
    deliberately stops making requests, and reloading then would re-inject
    a session the user had just dismissed. Reloading therefore requires a
    live session to exist as well, which a disconnected page does not have.

    A reload has to be followed by its own injection step, which is why
    "reinject" exists as a separate answer. `window.load_url` does *not*
    re-fire pywebview's `loaded` event (measured on 6.2.1 / Windows: the
    handler runs once for the initial navigation and not again for a
    programmatic load of the same URL), so a reloaded page would otherwise
    come back with no credentials at all and sit there asking to be paired.
    """
    if reinject_at is not None and now >= reinject_at:
        return "reinject"
    if now - last_response > silence_limit:
        return "reload" if has_session else None
    if now >= renew_at:
        return "renew"
    return None


def main() -> None:
    import pystray
    import webview

    _patch_webview2_mic_permission()
    from app.core.config import Settings, get_settings
    settings = Settings(live_mode=True, local_agent_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings

    # log_config=None: don't let uvicorn install its own logging config over
    # the file-based one set up above — this is the only way errors in a
    # console=False windowed app end up anywhere visible at all.
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None, proxy_headers=False)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not server_thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Desktop backend did not start; no credentials were issued")
        time.sleep(0.05)

    remote_server = None
    if settings.remote_bind_host:
        address = ipaddress.ip_address(settings.remote_bind_host)
        if not address.is_private or address.is_unspecified or not settings.tls_certificate or not settings.tls_private_key:
            raise RuntimeError("Remote runtime needs a specific private address and TLS certificate/key")
        # A second listener on the SAME process/state, not another worker.
        remote_server = uvicorn.Server(uvicorn.Config(
            app, host=settings.remote_bind_host, port=settings.remote_port, lifespan="off",
            ssl_certfile=settings.tls_certificate, ssl_keyfile=settings.tls_private_key,
            proxy_headers=False, log_config=None, access_log=False,
        ))
        threading.Thread(target=remote_server.run, daemon=True).start()

    page_url = f"http://127.0.0.1:{PORT}/"
    window = webview.create_window("JARVIS", page_url, width=1280, height=800)

    # Renewal deadline, and the last sign of life from the page. Both are
    # written from the UI thread and read by the watchdog thread; a stale
    # read costs at most one extra tick, so no lock is warranted.
    schedule = {"renew_at": float("inf"), "last_response": time.monotonic(), "reinject_at": None}

    def inject_session():
        credentials = app.state.runtime.bootstrap(settings.local_device_id, settings.granted_permission_set)
        # Short-lived session delivered by the native host only to its trusted
        # page. No secret-bearing public HTTP route, URL, or persistent JS store.
        #
        # Retried from inside the page rather than fired once: after a reload
        # the document can exist before app.js has finished defining
        # jarvisBootstrap, and a single call would then be silently lost.
        window.run_js(
            "(function apply(tries) {"
            "  if (typeof window.jarvisBootstrap === 'function') {"
            "    window.jarvisBootstrap(" + json.dumps(credentials) + ");"
            "  } else if (tries > 0) {"
            "    setTimeout(function () { apply(tries - 1); }, 250);"
            "  }"
            "})(40)"
        )
        # Taken from the lifetime the runtime issued rather than restated
        # here, so the session TTL stays owned by one place.
        schedule["renew_at"] = time.monotonic() + credentials["expires_in_seconds"] * SESSION_RENEWAL_FRACTION

    def bootstrap_ui():
        if window.get_current_url() != page_url:
            return
        inject_session()

    window.events.loaded += bootstrap_ui

    def note_page_response(_response):
        # Runs on the UI thread for every HTTP response the page receives,
        # so it must stay this cheap. A 401 counts: the point is that the
        # renderer is still executing JavaScript, not that it is authorised.
        schedule["last_response"] = time.monotonic()

    window.events.response_received += note_page_response

    def watch_page():
        logger = logging.getLogger("jarvis.desktop")
        while not server.should_exit:
            time.sleep(WATCHDOG_INTERVAL_SECONDS)
            try:
                action = page_watchdog_action(
                    time.monotonic(), schedule["last_response"], schedule["renew_at"],
                    app.state.runtime.has_session(settings.local_device_id),
                    schedule["reinject_at"],
                )
                if action == "reload":
                    logger.warning("desktop page has made no request for %.0fs; reloading it",
                                   time.monotonic() - schedule["last_response"])
                    # Reset first: the reload needs a full silence window to
                    # prove itself, or every tick would reload again.
                    schedule["last_response"] = time.monotonic()
                    schedule["reinject_at"] = time.monotonic() + WATCHDOG_INTERVAL_SECONDS
                    window.load_url(page_url)
                elif action == "reinject":
                    schedule["reinject_at"] = None
                    inject_session()
                elif action == "renew":
                    inject_session()
            except Exception:
                # A watchdog that dies on one bad tick is worse than none,
                # because the window would look supervised and not be.
                logger.exception("desktop page watchdog tick failed")

    threading.Thread(target=watch_page, daemon=True).start()

    def on_closing():
        window.hide()
        return False  # cancel the actual close — minimize to tray instead

    window.events.closing += on_closing

    autostart_state = {"enabled": False}

    def open_dashboard(_icon=None, _item=None):
        window.show()

    def toggle_autostart(icon, item):
        autostart_state["enabled"] = not item.checked
        _set_autostart(autostart_state["enabled"])
        icon.update_menu()

    def quit_app(icon, _item):
        # Mirrors /v1/system/terminate's cleanup — Quit is another valid way
        # to end the whole app, and should leave the same clean state
        # (no orphaned JARVIS-controlled browser windows left running).
        import asyncio

        from app.services.browser_automation import get_browser_automation_service

        try:
            asyncio.run(get_browser_automation_service().close_all())
        except Exception:
            logging.getLogger("jarvis.desktop").exception("Failed to close browser automation on quit")
        server.should_exit = True
        if remote_server:
            remote_server.should_exit = True
        icon.stop()
        window.destroy()

    icon = pystray.Icon(
        "jarvis",
        _build_tray_icon(),
        "JARVIS",
        menu=pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
            pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda _item: autostart_state["enabled"]),
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()

    # debug=True enables the window's right-click "Inspect" / F12 DevTools —
    # temporarily on while diagnosing the packaged app's wake-word issue,
    # since nothing client-side otherwise has any visible console at all.
    # pywebview requires start() on the main thread on Windows.
    webview.start(debug=False)


def packaged_self_test():
    """Exercise packaged startup/auth without microphones, providers, or PC effects."""
    import tempfile

    from fastapi.testclient import TestClient

    from app.core.config import Settings, get_settings

    with tempfile.TemporaryDirectory(prefix="jarvis-package-check-") as directory:
        settings = Settings(_env_file=None, live_mode=True, local_agent_enabled=False,
                            runtime_database=str(Path(directory) / "runtime.db"),
                            database_url="sqlite:///" + str(Path(directory) / "memory.db").replace("\\", "/"),
                            allowed_hosts="testserver", allowed_origins="", granted_permissions="",
                            reasoning_provider="mock", vision_provider="mock", tts_provider="mock", stt_provider="mock")
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app, base_url="https://testserver") as client:
            credentials = app.state.runtime.bootstrap("desktop", [])
            headers = {"Authorization": "Bearer " + credentials["access_token"]}
            approval = {**headers, "X-Jarvis-Approval-Key": credentials["approval_key"]}
            assert credentials["access_token"] not in client.get("/").text
            assert client.get("/v2/capabilities").status_code == 401
            assert client.get("/v2/capabilities", headers=headers).status_code == 200
            proposal = client.post("/v1/tools/propose", headers=headers,
                                   json={"tool_name": "delete_memory", "target": "absent-test-memory"}).json()
            aid = proposal["action_id"]
            assert client.post(f"/v1/actions/{aid}/approve", headers=headers).status_code == 401
            assert client.post(f"/v1/actions/{aid}/approve", headers=approval).status_code == 200
            response = client.post("/v1/tools/execute", headers=headers, json={"action_id": aid})
            assert response.json()["success"] is True
        app.dependency_overrides.clear()
    Path("jarvis-self-test.txt").write_text("PASS: packaged startup, public shell, session auth, approval, isolated execution\n")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        packaged_self_test()
    else:
        main()
