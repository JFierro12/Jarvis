import app.gesture as gesture


def _spy():
    calls = []

    def poster(event):
        calls.append(event)

    return poster, calls


def test_move_cursor_relative_computes_expected_position(monkeypatch):
    monkeypatch.setattr(gesture, "get_cursor_position", lambda: (100.0, 100.0))
    captured = {}

    def fake_create_mouse_event(source, event_type, point, button):
        captured["event_type"] = event_type
        captured["point"] = point
        return "fake-move-event"

    monkeypatch.setattr(gesture.Quartz, "CGEventCreateMouseEvent", fake_create_mouse_event)

    poster, calls = _spy()
    gesture.move_cursor_relative(10.0, -5.0, poster=poster)

    assert captured["event_type"] == gesture.Quartz.kCGEventMouseMoved
    assert captured["point"] == (110.0, 95.0)
    assert calls == ["fake-move-event"]


def test_move_cursor_relative_clamps_to_screen_bounds(monkeypatch):
    class _Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    class _Size:
        def __init__(self, w, h):
            self.width = w
            self.height = h

    class _FakeRect:
        def __init__(self, x, y, w, h):
            self.origin = _Point(x, y)
            self.size = _Size(w, h)

    monkeypatch.setattr(gesture, "get_cursor_position", lambda: (0.0, 0.0))
    monkeypatch.setattr(gesture.Quartz, "CGMainDisplayID", lambda: 0)
    monkeypatch.setattr(gesture.Quartz, "CGDisplayBounds", lambda display_id: _FakeRect(0, 0, 1000, 800))

    captured = {}

    def fake_create_mouse_event(source, event_type, point, button):
        captured["point"] = point
        return "fake-move-event"

    monkeypatch.setattr(gesture.Quartz, "CGEventCreateMouseEvent", fake_create_mouse_event)

    poster, _ = _spy()
    gesture.move_cursor_relative(5000.0, 5000.0, poster=poster)

    assert captured["point"] == (999.0, 799.0)


def test_click_posts_down_then_up_at_current_position(monkeypatch):
    monkeypatch.setattr(gesture, "get_cursor_position", lambda: (42.0, 24.0))

    created = []

    def fake_create_mouse_event(source, event_type, point, button):
        created.append((event_type, point))
        return (event_type, point)

    monkeypatch.setattr(gesture.Quartz, "CGEventCreateMouseEvent", fake_create_mouse_event)

    poster, calls = _spy()
    gesture.click(poster=poster)

    assert created == [
        (gesture.Quartz.kCGEventLeftMouseDown, (42.0, 24.0)),
        (gesture.Quartz.kCGEventLeftMouseUp, (42.0, 24.0)),
    ]
    assert calls == created


def test_scroll_posts_correct_wheel_delta_sign(monkeypatch):
    captured = {}

    def fake_create_scroll_event(source, unit, wheel_count, wheel1):
        captured["wheel1"] = wheel1
        return "fake-scroll-event"

    monkeypatch.setattr(gesture.Quartz, "CGEventCreateScrollWheelEvent", fake_create_scroll_event)

    poster, calls = _spy()
    gesture.scroll(-18.0, poster=poster)

    assert captured["wheel1"] == -18
    assert calls == ["fake-scroll-event"]


def test_zoom_sets_command_flag_on_scroll_event(monkeypatch):
    monkeypatch.setattr(gesture.Quartz, "CGEventCreateScrollWheelEvent", lambda *a: "fake-zoom-event")

    flags_set = {}

    def fake_set_flags(event, flags):
        flags_set["event"] = event
        flags_set["flags"] = flags

    monkeypatch.setattr(gesture.Quartz, "CGEventSetFlags", fake_set_flags)

    poster, calls = _spy()
    gesture.zoom(6.5, poster=poster)

    assert flags_set["event"] == "fake-zoom-event"
    assert flags_set["flags"] == gesture.Quartz.kCGEventFlagMaskCommand
    assert calls == ["fake-zoom-event"]
