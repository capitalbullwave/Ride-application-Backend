"""Notification HTTP APIs — device tokens, inbox, test, admin broadcast."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin, get_current_driver, get_current_user
from app.core.exceptions import NotFoundException, ValidationException
from app.database.session import get_db
from app.models import AdminUser, Driver, User
from app.notifications.schemas import (
    AdminBroadcastRequest,
    DeviceTokenRequest,
    DeviceTokenResponse,
    TestNotificationRequest,
    TopicSubscribeRequest,
)
from app.notifications.service import (
    NotificationService,
    serialize_driver_notification,
    serialize_user_notification,
)
from app.services import firebase_notification_service as fcm

router = APIRouter(tags=["Notifications"])


@router.post("/users/device-token", response_model=DeviceTokenResponse)
async def update_user_device_token(
    data: DeviceTokenRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    topics = await NotificationService(db).update_user_device_token(
        user,
        fcm_token=data.fcm_token.strip(),
        device_type=data.device_type,
        device_id=data.device_id,
    )
    return DeviceTokenResponse(
        fcm_token=data.fcm_token.strip(),
        device_type=data.device_type,
        topics=topics,
    )


@router.post("/drivers/device-token", response_model=DeviceTokenResponse)
async def update_driver_device_token(
    data: DeviceTokenRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    topics = await NotificationService(db).update_driver_device_token(
        driver,
        fcm_token=data.fcm_token.strip(),
        device_type=data.device_type,
        device_id=data.device_id,
    )
    return DeviceTokenResponse(
        fcm_token=data.fcm_token.strip(),
        device_type=data.device_type,
        topics=topics,
    )


@router.post("/test")
async def send_test_notification(
    data: TestNotificationRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Send an immediate test push using the provided token or the caller's saved token."""
    token = (data.token or "").strip() or None
    if not token and data.user_id:
        target = await db.get(User, data.user_id)
        if not target:
            raise NotFoundException("User not found")
        token = target.fcm_token
    if not token and data.driver_id:
        target_d = await db.get(Driver, data.driver_id)
        if not target_d:
            raise NotFoundException("Driver not found")
        token = target_d.fcm_token
    if not token:
        token = user.fcm_token
    if not token:
        raise ValidationException("No FCM token available. Save device-token first or pass token.")

    payload = dict(data.data or {})
    payload.setdefault("type", "test")
    payload.setdefault("screen", payload.get("screen", "home"))
    payload.setdefault("user_id", str(user.id))

    result = fcm.send_to_token(
        token,
        data.title,
        data.body,
        payload,
        channel_id=data.channel_id,
        analytics_label="test",
    )
    return {"success": bool(result.get("success")), "result": result}


@router.get("")
async def list_my_notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = await NotificationService(db).list_for_user(user.id, page, page_size)
    return {
        "data": [serialize_user_notification(n) for n in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/unread")
async def list_unread_notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = await NotificationService(db).list_for_user(
        user.id, page, page_size, unread_only=True
    )
    return {
        "data": [serialize_user_notification(n) for n in items],
        "total": total,
        "unread_count": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/read-all")
async def mark_all_read(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    updated = await NotificationService(db).mark_all_user_notifications_read(user.id)
    return {"updated": updated}


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    notification = await NotificationService(db).mark_user_notification_read(notification_id, user.id)
    return serialize_user_notification(notification)


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    await NotificationService(db).delete_notification(notification_id, user_id=user.id)
    return {"deleted": True, "id": str(notification_id)}


@router.post("/topics/subscribe")
async def subscribe_topic(
    data: TopicSubscribeRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    result = fcm.subscribe_token(data.token, data.topic)
    return {"user_id": str(user.id), **result}


@router.post("/topics/unsubscribe")
async def unsubscribe_topic(
    data: TopicSubscribeRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    result = fcm.unsubscribe_token(data.token, data.topic)
    return {"user_id": str(user.id), **result}


@router.post("/admin/broadcast")
async def admin_broadcast(
    data: AdminBroadcastRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    service = NotificationService(db)
    payload = {
        **(data.data or {}),
        "type": "admin_announcement",
        "event": "admin_announcement",
        "screen": "notifications",
        "target": data.target,
    }
    sent = 0
    failed = 0

    if data.target == "user":
        if not data.user_id:
            raise ValidationException("user_id required")
        await service.notify_and_push(
            title=data.title,
            message=data.body,
            notification_type="ADMIN_BROADCAST",
            user_id=data.user_id,
            data=payload,
            channel_id=data.channel_id,
        )
        sent = 1
    elif data.target == "driver":
        if not data.driver_id:
            raise ValidationException("driver_id required")
        await service.notify_and_push(
            title=data.title,
            message=data.body,
            notification_type="ADMIN_BROADCAST",
            driver_id=data.driver_id,
            data=payload,
            channel_id=data.channel_id,
        )
        sent = 1
    elif data.target == "city":
        if not data.city_id:
            raise ValidationException("city_id required")
        result = fcm.send_to_topic(
            fcm.city_topic(data.city_id),
            data.title,
            data.body,
            payload,
            channel_id=data.channel_id,
        )
        sent = 1 if result.get("success") else 0
        failed = 0 if result.get("success") else 1
        await service.create_in_app(
            title=data.title,
            message=data.body,
            notification_type="ADMIN_BROADCAST",
            data={**payload, "city_id": data.city_id},
            status="sent" if result.get("success") else "failed",
        )
    else:
        topic_map = {
            "all_users": fcm.TOPIC_ALL_USERS,
            "all_drivers": fcm.TOPIC_ALL_DRIVERS,
            "promotion": fcm.TOPIC_PROMOTION,
            "news": fcm.TOPIC_NEWS,
            "maintenance": fcm.TOPIC_MAINTENANCE,
        }
        topic = topic_map.get(data.target)
        if not topic:
            raise ValidationException("Unsupported broadcast target")

        # Persist individual inbox rows for all_users / all_drivers (paginated, capped).
        if data.target == "all_users":
            result = await db.execute(select(User).where(User.is_deleted.is_(False)).limit(500))
            for u in result.scalars().all():
                await service.notify_and_push(
                    title=data.title,
                    message=data.body,
                    notification_type="ADMIN_BROADCAST",
                    user_id=u.id,
                    data=payload,
                    channel_id=data.channel_id,
                )
                sent += 1
        elif data.target == "all_drivers":
            result = await db.execute(select(Driver).where(Driver.is_deleted.is_(False)).limit(500))
            for d in result.scalars().all():
                await service.notify_and_push(
                    title=data.title,
                    message=data.body,
                    notification_type="ADMIN_BROADCAST",
                    driver_id=d.id,
                    data=payload,
                    channel_id=data.channel_id,
                )
                sent += 1
        else:
            result = fcm.send_to_topic(
                topic,
                data.title,
                data.body,
                payload,
                channel_id=data.channel_id,
            )
            await service.create_in_app(
                title=data.title,
                message=data.body,
                notification_type="ADMIN_BROADCAST",
                data=payload,
                status="sent" if result.get("success") else "failed",
            )
            sent = 1 if result.get("success") else 0
            failed = 0 if result.get("success") else 1

    return {
        "success": failed == 0,
        "admin_id": str(admin.id),
        "target": data.target,
        "sent": sent,
        "failed": failed,
    }


# Convenience mirror so Flutter panels can hit legacy-style paths via include in api_router.
driver_inbox_router = APIRouter(tags=["Notifications"])


@driver_inbox_router.get("/notifications")
async def driver_list_notifications_alias(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    items, total, unread_count = await NotificationService(db).list_for_driver(driver.id, page, page_size)
    return {
        "data": [serialize_driver_notification(n) for n in items],
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "page_size": page_size,
    }
