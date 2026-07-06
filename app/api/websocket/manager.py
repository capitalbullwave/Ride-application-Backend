from typing import Dict, Set
from uuid import UUID

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_connections: Dict[str, Set[str]] = {}
        self.ride_subscribers: Dict[str, Set[str]] = {}

    async def connect(self, websocket: WebSocket, connection_id: str, user_id: str) -> None:
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(connection_id)

    def disconnect(self, connection_id: str, user_id: str) -> None:
        self.active_connections.pop(connection_id, None)
        if user_id in self.user_connections:
            self.user_connections[user_id].discard(connection_id)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

    def subscribe_ride(self, connection_id: str, ride_id: str) -> None:
        if ride_id not in self.ride_subscribers:
            self.ride_subscribers[ride_id] = set()
        self.ride_subscribers[ride_id].add(connection_id)

    def unsubscribe_ride(self, connection_id: str, ride_id: str) -> None:
        if ride_id in self.ride_subscribers:
            self.ride_subscribers[ride_id].discard(connection_id)

    async def send_personal(self, user_id: str, message: dict) -> None:
        connections = self.user_connections.get(user_id, set())
        for conn_id in connections:
            ws = self.active_connections.get(conn_id)
            if ws:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def broadcast_ride(self, ride_id: str, message: dict) -> None:
        subscribers = self.ride_subscribers.get(ride_id, set())
        for conn_id in subscribers:
            ws = self.active_connections.get(conn_id)
            if ws:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def broadcast_all(self, message: dict) -> None:
        for ws in self.active_connections.values():
            try:
                await ws.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()
