import json

import pytest
from starlette.websockets import WebSocketDisconnect

import app.gesture_ws as gesture_ws
from tests.conftest import AUTH_HEADERS


@pytest.fixture(autouse=True)
def _reset_connection_flag():
    gesture_ws._connection_active = False
    yield
    gesture_ws._connection_active = False


def test_missing_auth_header_is_rejected_before_accept(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/gesture/stream"):
            pass


def test_invalid_token_is_rejected_before_accept(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/gesture/stream", headers={"Authorization": "Bearer wrong-token"}):
            pass


def test_cursor_delta_dispatches_move_cursor_relative(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(gesture_ws, "move_cursor_relative", lambda dx, dy: captured.update(dx=dx, dy=dy))

    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS) as websocket:
        websocket.send_text(json.dumps({"type": "cursor_delta", "dx": 1.5, "dy": -2.5}))

    assert captured == {"dx": 1.5, "dy": -2.5}


def test_click_dispatches_click(client, monkeypatch):
    captured = {"called": False}
    monkeypatch.setattr(gesture_ws, "click", lambda: captured.update(called=True))

    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS) as websocket:
        websocket.send_text(json.dumps({"type": "click"}))

    assert captured["called"] is True


def test_scroll_dispatches_scroll(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(gesture_ws, "scroll", lambda dy: captured.update(dy=dy))

    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS) as websocket:
        websocket.send_text(json.dumps({"type": "scroll", "dy": -18.0}))

    assert captured == {"dy": -18.0}


def test_zoom_dispatches_zoom(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(gesture_ws, "zoom", lambda delta: captured.update(delta=delta))

    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS) as websocket:
        websocket.send_text(json.dumps({"type": "zoom", "delta": 6.5}))

    assert captured == {"delta": 6.5}


def test_malformed_and_unknown_messages_are_dropped_not_fatal(client, monkeypatch):
    captured = {"called": False}
    monkeypatch.setattr(gesture_ws, "click", lambda: captured.update(called=True))

    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS) as websocket:
        websocket.send_text("not json at all")
        websocket.send_text(json.dumps({"type": "unknown_gesture_type"}))
        # The connection must still be alive after two bad messages —
        # a valid one sent afterward should still be processed.
        websocket.send_text(json.dumps({"type": "click"}))

    assert captured["called"] is True


def test_single_connection_cap_rejects_a_second_concurrent_connection(client):
    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS):
                pass

    # The slot frees up once the first connection closes.
    with client.websocket_connect("/gesture/stream", headers=AUTH_HEADERS):
        pass
