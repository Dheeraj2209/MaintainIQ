"""Tests for the /api/ws WebSocket handshake (src/realtime)."""
from starlette.websockets import WebSocketDisconnect


def test_websocket_rejects_unauthenticated(anon_client):
    try:
        with anon_client.websocket_connect("/api/ws"):
            assert False, "expected the handshake to be rejected"
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_websocket_accepts_authenticated_session(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.close()
