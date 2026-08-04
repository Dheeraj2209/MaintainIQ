from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.api.deps import get_db
from src.auth.deps import get_user_from_token
from src.auth.security import COOKIE_NAME
from src.realtime.manager import manager

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, conn=Depends(get_db)) -> None:
    token = websocket.cookies.get(COOKIE_NAME)
    user = get_user_from_token(conn, token)
    conn.close()  # only needed for the handshake auth check, not the socket's lifetime

    if user is None:
        await websocket.close(code=4401)
        return

    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
