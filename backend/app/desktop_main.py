"""Entry point for the packaged Windows desktop app (see desktop/README.md).

Not used by the normal `uvicorn app.main:app` dev/deploy path — this wraps
the same ASGI app in a native window + system tray so it can run 24/7 as a
background app rather than a terminal process. PyInstaller's entry point is
this module (see desktop/jarvis.spec).
"""

import sys
import threading

import uvicorn

from app.main import app

PORT = 8756


def _build_tray_icon():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(94, 203, 245, 255))
    return image


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


def main() -> None:
    import pystray
    import webview

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
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

    # pywebview requires start() on the main thread on Windows.
    webview.start()


if __name__ == "__main__":
    main()
