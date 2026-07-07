"""In-ride chat between passenger and driver."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.websocket.manager import manager
from app.core.constants import DRIVER_ACTIVE_RIDE_STATUSES, RideStatus
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.models import Driver, Ride, User
from app.rides.models import ChatMessage

CHAT_SEND_STATUSES = frozenset(DRIVER_ACTIVE_RIDE_STATUSES)


def serialize_chat_message(
    msg: ChatMessage,
    *,
    sender_name: Optional[str] = None,
) -> dict:
    return {
        "id": str(msg.id),
        "ride_id": str(msg.ride_id),
        "sender_id": str(msg.sender_id),
        "sender_type": msg.sender_type,
        "sender_name": sender_name,
        "message": msg.message,
        "is_read": msg.is_read,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


class RideChatService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_ride(self, ride_id: UUID) -> Ride:
        result = await self.db.execute(
            select(Ride)
            .options(selectinload(Ride.user), selectinload(Ride.driver))
            .where(Ride.id == ride_id)
        )
        ride = result.scalar_one_or_none()
        if not ride:
            raise NotFoundException("Ride not found")
        return ride

    @staticmethod
    def _sender_name(ride: Ride, sender_type: str, sender_id: UUID) -> str:
        if sender_type == "user" and ride.user:
            return f"{ride.user.first_name} {ride.user.last_name}".strip() or "Passenger"
        if sender_type == "driver" and ride.driver:
            return f"{ride.driver.first_name} {ride.driver.last_name}".strip() or "Driver"
        return "User" if sender_type == "user" else "Driver"

    async def list_messages(self, ride_id: UUID, *, limit: int = 200) -> list[dict]:
        ride = await self._load_ride(ride_id)
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.ride_id == ride_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(min(limit, 500))
        )
        messages = result.scalars().all()
        return [
            serialize_chat_message(
                msg,
                sender_name=self._sender_name(ride, msg.sender_type, msg.sender_id),
            )
            for msg in messages
        ]

    async def send_message(
        self,
        ride_id: UUID,
        *,
        sender_id: UUID,
        sender_type: str,
        message: str,
    ) -> dict:
        text = (message or "").strip()
        if len(text) < 1:
            raise ValidationException("Message cannot be empty")
        if len(text) > 1000:
            raise ValidationException("Message is too long (max 1000 characters)")

        sender_type = sender_type.lower()
        if sender_type not in {"user", "driver"}:
            raise ValidationException("Invalid sender type")

        ride = await self._load_ride(ride_id)
        if ride.status == RideStatus.CANCELLED.value:
            raise ValidationException("Cannot chat on a cancelled ride")
        if ride.status not in CHAT_SEND_STATUSES:
            raise ValidationException("Chat is only available during an active ride")

        if sender_type == "user":
            if ride.user_id != sender_id:
                raise ForbiddenException("Access denied")
            if not ride.driver_id:
                raise ValidationException("Driver not assigned yet")
        else:
            if ride.driver_id != sender_id:
                raise ForbiddenException("Access denied")

        chat = ChatMessage(
            ride_id=ride_id,
            sender_id=sender_id,
            sender_type=sender_type,
            message=text,
            is_read=False,
        )
        self.db.add(chat)
        await self.db.flush()
        await self.db.refresh(chat)

        sender_name = self._sender_name(ride, sender_type, sender_id)
        payload = serialize_chat_message(chat, sender_name=sender_name)

        ws_payload = {
            "event": "chat_message",
            "ride_id": str(ride_id),
            **payload,
        }

        try:
            await manager.broadcast_ride(str(ride_id), ws_payload)
            await manager.send_personal(str(ride.user_id), ws_payload)
            if ride.driver_id:
                await manager.send_personal(str(ride.driver_id), ws_payload)
        except Exception:
            pass

        return payload
