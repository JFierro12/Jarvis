"""macOS mouse/cursor control via Quartz CGEvent synthesis.

Deliberately a separate module from `commands.py`: this accepts continuous,
dynamic numeric input (cursor deltas, scroll amounts) driving live mouse
control, which is a meaningfully larger trust boundary than commands.py's
fixed-argv allowlist (spec 19) — a caller here can move the cursor and click
anywhere, not just run one of a handful of pre-approved commands. It is
still gated by the same pairing-token auth as everything else (see
gesture_ws.py), just not folded into the allowlist model itself.

Known limitation: there is no public macOS API to synthesize a true
trackpad pinch/magnify gesture. `zoom()` sends a Cmd+scroll-wheel event
instead, which many apps interpret as zoom (Safari, Preview, Maps, Xcode,
Figma) but not universally (e.g. Finder's icon view ignores it). This is an
accepted limitation, not a bug to chase further.
"""

from typing import Callable, Optional, Tuple

try:
    import Quartz
except ImportError:  # pragma: no cover - only reachable on non-macOS hosts
    # This module is macOS-only by design, but importing it must not crash
    # the whole app (or test collection) on Windows/Linux. pc-agent's other
    # routes - the fixed-argv command allowlist, status, auth - are
    # cross-platform and have to stay reachable. Same lazy-import treatment
    # pywinauto gets on the Windows side.
    Quartz = None  # type: ignore[assignment]

Poster = Callable[[object], None]


class GestureUnavailableError(RuntimeError):
    """Cursor synthesis was requested on a host that cannot perform it."""


def is_available() -> bool:
    """True only where real CGEvent synthesis is possible."""
    return Quartz is not None


def _require_quartz() -> None:
    if Quartz is None:
        raise GestureUnavailableError(
            "Cursor and gesture synthesis requires macOS Quartz (pyobjc); "
            "this host cannot synthesize input events."
        )


def get_cursor_position() -> Tuple[float, float]:
    _require_quartz()
    event = Quartz.CGEventCreate(None)
    location = Quartz.CGEventGetLocation(event)
    return (location.x, location.y)


def _clamp_to_screen(x: float, y: float) -> Tuple[float, float]:
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    clamped_x = min(max(x, bounds.origin.x), bounds.origin.x + bounds.size.width - 1)
    clamped_y = min(max(y, bounds.origin.y), bounds.origin.y + bounds.size.height - 1)
    return (clamped_x, clamped_y)


def _default_poster(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _post(event, poster: Optional[Poster]) -> None:
    # Resolved at call time (not a bound default), same convention as
    # commands.py's execute_command(command_id, runner=None) — lets tests
    # monkeypatch per-call without a real Quartz post ever happening.
    active_poster = poster or _default_poster
    active_poster(event)


def move_cursor_relative(dx: float, dy: float, poster: Optional[Poster] = None) -> None:
    _require_quartz()
    current_x, current_y = get_cursor_position()
    target_x, target_y = _clamp_to_screen(current_x + dx, current_y + dy)
    event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (target_x, target_y), Quartz.kCGMouseButtonLeft)
    _post(event, poster)


def click(poster: Optional[Poster] = None) -> None:
    _require_quartz()
    x, y = get_cursor_position()
    down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft)
    up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft)
    _post(down, poster)
    _post(up, poster)


def scroll(dy: float, poster: Optional[Poster] = None) -> None:
    _require_quartz()
    event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1, int(dy))
    _post(event, poster)


def zoom(delta: float, poster: Optional[Poster] = None) -> None:
    _require_quartz()
    event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1, int(delta))
    Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
    _post(event, poster)
