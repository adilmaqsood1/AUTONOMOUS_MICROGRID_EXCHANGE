"""WebSocket endpoint for real-time microgrid exchange event streaming."""
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_token
from app.websocket.hub import hub

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_events_endpoint(websocket: WebSocket,
                                    microgrid_id: Optional[str] = Query(None),
                                    channels: Optional[str] = Query(None),
                                    token: Optional[str] = Query(None)):
    # Optional token verification if provided
    if token:
        try:
            decode_token(token)
        except ValueError:
            await websocket.close(code=4001, reason="invalid token")
            return

    channel_set = {c.strip() for c in channels.split(",") if c.strip()} if channels else None
    client = await hub.connect(websocket, microgrid_id, channel_set)
    try:
        # Keep connection open and receive optional client messages/pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        hub.disconnect(client)
    except Exception:
        hub.disconnect(client)
