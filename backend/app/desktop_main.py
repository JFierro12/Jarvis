"""Entry point for the packaged Windows desktop app (see desktop/README.md).

Not used by the normal `uvicorn app.main:app` dev/deploy path — this wraps
the same ASGI app in a native window + system tray so it can run 24/7 as a
background app rather than a terminal process. PyInstaller's entry point is
this module (see desktop/jarvis.spec).
"""

import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn

PORT = 8756

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
_LOG_PATH = Path(sys.executable).resolve().parent / "jarvis.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(_LOG_PATH, encoding="utf-8")],
)

from app.main import app  # noqa: E402 — must follow the logging setup above


def _build_tray_icon():
    from PIL import Image

    # Bundled via jarvis.spec's `datas` — sys._MEIPASS is PyInstaller's
    # runtime extraction root (present in both --onedir and --onefile
    # builds), not something this module has any other reason to reference.
    icon_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "assets" / "icon.png"
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
        # Import order matters: `Microsoft.Web.WebView2.Core` only becomes
        # importable once edgechromium.py's own clr.AddReference() calls
        # have run, which happens as a side effect of importing it — so it
        # must be imported first, not the other way around.
        from webview.platforms.edgechromium import EdgeChrome
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState
    except Exception:
        logging.getLogger("jarvis.desktop").exception("Could not patch WebView2 mic permission handling")
        return

    original_on_webview_ready = EdgeChrome.on_webview_ready

    def patched_on_webview_ready(self, sender, args):
        original_on_webview_ready(self, sender, args)

        def on_permission_requested(_sender, e):
            if e.get_PermissionKind() == CoreWebView2PermissionKind.Microphone:
                e.set_State(CoreWebView2PermissionState.Allow)

        try:
            sender.CoreWebView2.PermissionRequested += on_permission_requested
        except Exception:
            logging.getLogger("jarvis.desktop").exception("Failed to attach WebView2 PermissionRequested handler")

    EdgeChrome.on_webview_ready = patched_on_webview_ready


def main() -> None:
    import pystray
    import webview

    _patch_webview2_mic_permission()

    # log_config=None: don't let uvicorn install its own logging config over
    # the file-based one set up above — this is the only way errors in a
    # console=False windowed app end up anywhere visible at all.
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    window = webview.create_window("JARVIS", f"http://127.0.0.1:{PORT}/", width=1280, height=800)

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
    webview.start(debug=True)


if __name__ == "__main__":
    main()
