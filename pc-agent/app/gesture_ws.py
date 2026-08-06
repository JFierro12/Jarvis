import logging

from fastapi import Depends, WebSocket, WebSocketDisconnect, status

from app.config import Settings, get_settings
from app.gesture import click, move_cursor_relative, scroll, zoom
from app.gesture_schemas import ClickMessage, CursorDeltaMessage, GestureMessageAdapter, ScrollMessage, ZoomMessage

logger = logging.getLogger(__name__)

# Single-concurrent-connection cap, module state — the bespoke substitute
# for ratelimit.py's per-source-IP sliding window, which doesn't map onto a
# persistent connection (there's no per-message "request" to rate-limit
# against once a WebSocket is open).
_connection_active = False


async def gesture_stream(websocket: WebSocket, settings: Settings = Depends(get_settings)) -> None:
    global _connection_active

    # HTTPBearer/require_pairing_token expect an HTTP Request, not a
    # WebSocket, so auth is validated directly against the handshake
    # headers here instead — same Authorization: Bearer convention, just
    # checked before accept() rather than via a route Depends.
    auth_header = websocket.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer ") or auth_header[7:] != settings.pairing_token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if _connection_active:
        await websocket.close(code=1013)  # "Try Again Later"
        return

    await websocket.accept()
    _connection_active = True
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = GestureMessageAdapter.validate_json(raw)
            except Exception:
                logger.warning("gesture_stream: dropping malformed message")
                continue
            if isinstance(message, CursorDeltaMessage):
                move_cursor_relative(message.dx, message.dy)
            elif isinstance(message, ClickMessage):
                click()
            elif isinstance(message, ScrollMessage):
                scroll(message.dy)
            elif isinstance(message, ZoomMessage):
                zoom(message.delta)
    except WebSocketDisconnect:
        pass
    finally:
        _connection_active = False
