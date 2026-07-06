"""WebSocket endpoints — /ws/*"""
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.websocket.manager import manager
from app.core.security import decode_token

router = APIRouter(tags=["WebSocket"])


async def _connect(websocket: WebSocket, token: str) -> tuple[str, str] | None:
    try:
        payload = decode_token(token)
        user_id = payload["sub"]
    except ValueError:
        await websocket.close(code=4001)
        return None
    connection_id = str(uuid.uuid4())
    await manager.connect(websocket, connection_id, user_id)
    return connection_id, user_id


async def _listen(websocket: WebSocket, connection_id: str, user_id: str, channel: str) -> None:
    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")
            if event == "subscribe_ride":
                manager.subscribe_ride(connection_id, data.get("ride_id"))
            elif event == "unsubscribe_ride":
                manager.unsubscribe_ride(connection_id, data.get("ride_id"))
            elif event == "ping":
                await websocket.send_json({"event": "pong", "channel": channel})
            elif event == "location_update":
                await manager.broadcast_ride(data.get("ride_id"), {
                    "event": "location_update",
                    "channel": channel,
                    "lat": data.get("lat"),
                    "lng": data.get("lng"),
                    "ride_id": data.get("ride_id"),
                })
            elif event == "chat_message":
                await manager.broadcast_ride(data.get("ride_id"), {
                    "event": "chat_message",
                    "channel": channel,
                    "message": data.get("message"),
                    "ride_id": data.get("ride_id"),
                })
            elif event == "notification_ack":
                await websocket.send_json({"event": "notification_ack", "id": data.get("id")})
    except WebSocketDisconnect:
        manager.disconnect(connection_id, user_id)


@router.websocket("/ride")
async def ws_ride(websocket: WebSocket, token: str = Query(...)):
    connected = await _connect(websocket, token)
    if not connected:
        return
    connection_id, user_id = connected
    await _listen(websocket, connection_id, user_id, "ride")


@router.websocket("/location")
async def ws_location(websocket: WebSocket, token: str = Query(...)):
    connected = await _connect(websocket, token)
    if not connected:
        return
    connection_id, user_id = connected
    await _listen(websocket, connection_id, user_id, "location")


@router.websocket("/chat")
async def ws_chat(websocket: WebSocket, token: str = Query(...)):
    connected = await _connect(websocket, token)
    if not connected:
        return
    connection_id, user_id = connected
    await _listen(websocket, connection_id, user_id, "chat")


@router.websocket("/notification")
async def ws_notification(websocket: WebSocket, token: str = Query(...)):
    connected = await _connect(websocket, token)
    if not connected:
        return
    connection_id, user_id = connected
    await _listen(websocket, connection_id, user_id, "notification")
