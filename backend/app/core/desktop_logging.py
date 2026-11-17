"""Log destination for the packaged desktop app.

Separate from `desktop_main` on purpose: importing that module configures
logging and pulls in the whole ASGI app, so a test that wanted to check the
log path would have to write a real log file into the user's profile to do
it. These are pure functions with no import-time effects.

The packaged app is built with `console=False`, so there is no stdout or
stderr for anyone to read. That makes the log file the only channel through
which a failure can be reported -- and makes a failure to *open* that file
the worst possible error, because it is unreportable.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

APP_DIR_NAME = "Jarvis"
LOG_FILE_NAME = "jarvis.log"


def log_directory(platform: Optional[str] = None, env: Optional[dict] = None) -> Path:
    """The per-user directory the desktop app writes its log into.

    Deliberately *not* next to the executable, which is where this used to go
    via `Path(sys.executable).parent`. That had two failure modes:

    * Running from source, `sys.executable` is the interpreter, so the log
      landed inside `.venv/Scripts/` -- confusing, and wiped by any venv
      rebuild.
    * Installed under `Program Files`, that directory is not writable by the
      running user on a default Windows setup. `logging.FileHandler` then
      raised during module import, so the app failed to start and the only
      record of why would have been the log it could not open.

    `%LOCALAPPDATA%` is the documented location for this on Windows and is
    always writable by the user running the app. The same reasoning gives
    `$XDG_STATE_HOME` elsewhere.
    """
    platform = platform if platform is not None else sys.platform
    env = env if env is not None else os.environ

    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = env.get("XDG_STATE_HOME")
        root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_DIR_NAME


def log_path(platform: Optional[str] = None, env: Optional[dict] = None) -> Path:
    return log_directory(platform, env) / LOG_FILE_NAME


def build_handlers(path: Optional[Path] = None) -> List[logging.Handler]:
    """Handlers for the desktop app, which never raise.

    A logging destination that cannot be opened must not be the reason the
    app fails to start. On any `OSError` -- unwritable directory, read-only
    volume, path too long, file locked by another instance -- this degrades to
    a stream handler rather than propagating. That handler goes nowhere in a
    windowed build, which is no worse than the crash it replaces, and is
    genuinely useful when the app is run from a terminal for debugging.
    """
    target = path if path is not None else log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        return [logging.FileHandler(target, encoding="utf-8")]
    except OSError:
        return [logging.StreamHandler()]


__all__ = [
    "APP_DIR_NAME",
    "LOG_FILE_NAME",
    "build_handlers",
    "log_directory",
    "log_path",
]
