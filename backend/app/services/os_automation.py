import subprocess
import sys
from typing import List, Optional

# A short list of common apps by the name someone would actually say out
# loud ("open notepad") rather than the exact executable name. Anything not
# in this list is passed through as a literal command/executable — this is
# a convenience lookup, not an allowlist (unlike pc-agent's command
# allowlist, launching an arbitrary named/pathed executable is the intended
# capability here, gated by the tool's own confirmation/policy tier instead).
KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "settings": "start ms-settings:",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe",
    "spotify": "spotify.exe",
}


class OSAutomationError(Exception):
    pass


def _desktop():
    # Imported lazily, not at module top: pywinauto is Windows-only
    # (win32-specific dependency, see pyproject.toml), and this module is
    # imported unconditionally by app/api/tools.py — a top-level import
    # here would crash the entire backend at startup on any non-Windows
    # deployment (e.g. the Railway cloud target), not just fail this one
    # tool. win32 backend (classic Win32 API), not uia — a full UI
    # Automation tree walk over every top-level window (~250+ on a typical
    # desktop, including invisible/system ones) was noticeably slower in
    # testing than win32's direct enumeration. uia is the right choice for
    # a later round that needs to click/type into specific controls inside
    # a window, which win32 can't do — this round only needs
    # launch/find/focus/close by title, so win32 is sufficient and faster.
    if sys.platform != "win32":
        raise OSAutomationError("Desktop automation is only supported on Windows")
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise OSAutomationError("pywinauto is not installed") from exc
    return Desktop(backend="win32")


def launch_app(name: str) -> str:
    key = name.strip().lower()
    command = KNOWN_APPS.get(key, name.strip())
    if not command:
        raise OSAutomationError("No application name given")
    try:
        subprocess.Popen(command, shell=True)
    except Exception as exc:
        raise OSAutomationError(f'Could not launch "{name}": {exc}') from exc
    return f"Launched {name}."


def _visible_windows():
    for w in _desktop().windows():
        try:
            if w.is_visible() and w.window_text().strip():
                yield w
        except Exception:
            # A window can close mid-enumeration (races with is_visible()/
            # window_text() calls) — skip it rather than failing the whole
            # listing over one disappeared window.
            continue


def list_open_windows() -> List[str]:
    return [w.window_text() for w in _visible_windows()]


def _find_window(name: str):
    name_lower = name.strip().lower()
    if not name_lower:
        return None
    for w in _visible_windows():
        if name_lower in w.window_text().lower():
            return w
    return None


def focus_app(name: str) -> str:
    window = _find_window(name)
    if window is None:
        raise OSAutomationError(f'No open window matching "{name}"')
    title = window.window_text()
    window.set_focus()
    return f"Focused {title}."


def close_app(name: str) -> str:
    # window.close() sends the same close signal as clicking the window's
    # own X button — an app with unsaved work may show its own "Save
    # changes?" dialog in response, same as it would for a human closing
    # it. That dialog isn't detected or answered here; it's a known
    # limitation, not a silent data-loss risk (nothing is force-killed).
    window = _find_window(name)
    if window is None:
        raise OSAutomationError(f'No open window matching "{name}"')
    title = window.window_text()
    window.close()
    return f"Closed {title}."


def take_screenshot_bytes() -> bytes:
    import io

    from PIL import ImageGrab

    image = ImageGrab.grab()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
