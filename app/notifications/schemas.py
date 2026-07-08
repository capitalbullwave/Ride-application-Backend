"""Notification request/response DTOs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DeviceTokenRequest(BaseModel):
    fcm_token: str = Field(..., min_length=10, max_length=500)
    device_type: Literal["android", "ios", "web"] = "android"
    device_id: Optional[str] = Field(None, max_length=255)


class DeviceTokenResponse(BaseModel):
    success: bool = True
    message: str = "Device token updated"
    fcm_token: str
    device_type: str
    topics: list[str] = Field(default_factory=list)


class TestNotificationRequest(BaseModel):
    token: Optional[str] = Field(None, max_length=500)
    user_id: Optional[UUID] = None
    driver_id: Optional[UUID] = None
    title: str = "Test Notification"
    body: str = "Hello from Bull Wave Rides"
    data: dict[str, Any] = Field(default_factory=lambda: {"screen": "home", "type": "test"})
    channel_id: str = "admin"


class TopicSubscribeRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=500)
    topic: str = Field(..., min_length=1, max_length=100)


class AdminBroadcastRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=2000)
    target: Literal[
        "all_users",
        "all_drivers",
        "city",
        "user",
        "driver",
        "promotion",
        "news",
        "maintenance",
    ] = "all_users"
    city_id: Optional[str] = None
    user_id: Optional[UUID] = None
    driver_id: Optional[UUID] = None
    data: dict[str, Any] = Field(default_factory=dict)
    channel_id: str = "admin"


class NotificationItemResponse(BaseModel):
    id: UUID
    title: str
    body: str
    type: str
    user_id: Optional[UUID] = None
    driver_id: Optional[UUID] = None
    ride_id: Optional[str] = None
    booking_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    is_read: bool
    created_at: datetime
    sent_at: Optional[datetime] = None
    status: Optional[str] = None
