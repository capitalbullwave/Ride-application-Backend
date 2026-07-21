"""Notification service - Push, In-App, FCM, ride lifecycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.models import Driver, Notification, User
from app.services import firebase_notification_service as fcm

logger = get_logger(__name__)

_DRIVER_TYPE_MAP = {
    "RIDE": "ride",
    "PROMO": "offer",
    "PAYMENT": "bonus",
    "SYSTEM": "system",
    "ADMIN": "system",
    "ADMIN_BROADCAST": "system",
    "CHAT": "system",
    "WALLET": "bonus",
}

_EVENT_SCREEN_MAP = {
    "ride_request": "ride_request",
    "ride_accepted": "live_tracking",
    "driver_arrived": "live_tracking",
    "ride_started": "live_tracking",
    "ride_completed": "ride_summary",
    "ride_cancelled": "home",
    "wallet_credit": "wallet",
    "wallet_debit": "wallet",
    "payment_success": "wallet",
    "promotion": "offers",
    "subscription": "subscription",
    "admin_announcement": "notifications",
}

_CHANNEL_BY_TYPE = {
    "RIDE": "ride",
    "WALLET": "wallet",
    "PAYMENT": "wallet",
    "PROMO": "promotion",
    "ADMIN": "admin",
    "ADMIN_BROADCAST": "admin",
    "SYSTEM": "admin",
}


def _utc_now_naive() -> datetime:
    """notifications.read_at / sent_at are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def map_driver_notification_type(notification_type: str) -> str:
    return _DRIVER_TYPE_MAP.get((notification_type or "SYSTEM").upper(), "system")


def serialize_driver_notification(notification: Notification) -> dict:
    return {
        "id": str(notification.id),
        "title": notification.title,
        "body": notification.message,
        "type": map_driver_notification_type(notification.notification_type),
        "read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
        "data": notification.data,
        "status": getattr(notification, "status", None),
    }


def serialize_user_notification(notification: Notification) -> dict:
    data = notification.data or {}
    return {
        "id": str(notification.id),
        "title": notification.title,
        "message": notification.message,
        "body": notification.message,
        "type": notification.notification_type,
        "read": notification.is_read,
        "is_read": notification.is_read,
        "time": notification.created_at.isoformat() if notification.created_at else None,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "data": data,
        "status": getattr(notification, "status", None),
    }


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _enrich_data(
        self,
        data: dict | None,
        *,
        notification_type: str,
        event: str | None = None,
        user_id: UUID | None = None,
        driver_id: UUID | None = None,
        ride_id: str | None = None,
        booking_id: str | None = None,
        screen: str | None = None,
        priority: str = "high",
    ) -> dict[str, Any]:
        payload = dict(data or {})
        evt = event or payload.get("event") or notification_type.lower()
        payload.setdefault("type", evt)
        payload.setdefault("event", evt)
        payload.setdefault("notification_id", str(uuid4()))
        payload.setdefault("timestamp", str(int(datetime.now(timezone.utc).timestamp())))
        payload.setdefault("priority", priority)
        payload.setdefault("screen", screen or _EVENT_SCREEN_MAP.get(str(evt), "notifications"))
        if user_id:
            payload.setdefault("user_id", str(user_id))
        if driver_id:
            payload.setdefault("driver_id", str(driver_id))
        if ride_id:
            payload.setdefault("ride_id", str(ride_id))
        if booking_id:
            payload.setdefault("booking_id", str(booking_id))
        elif ride_id:
            payload.setdefault("booking_id", str(ride_id))
        return payload

    async def create_in_app(
        self,
        title: str,
        message: str,
        notification_type: str = "SYSTEM",
        user_id: UUID | None = None,
        driver_id: UUID | None = None,
        data: dict | None = None,
        *,
        status: str = "pending",
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            driver_id=driver_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data=data,
            status=status,
        )
        self.db.add(notification)
        await self.db.flush()
        await self.db.refresh(notification)
        return notification

    async def _clear_invalid_token(self, *, user_id: UUID | None = None, driver_id: UUID | None = None) -> None:
        if user_id:
            user = await self.db.get(User, user_id)
            if user:
                logger.warning("fcm_token_cleared_invalid", user_id=str(user_id))
                user.fcm_token = None
        if driver_id:
            driver = await self.db.get(Driver, driver_id)
            if driver:
                logger.warning("fcm_token_cleared_invalid", driver_id=str(driver_id))
                driver.fcm_token = None
        await self.db.flush()

    async def _mark_delivery(self, notification: Notification | None, result: dict) -> None:
        if notification is None:
            return
        if result.get("success"):
            notification.status = "sent"
            notification.sent_at = _utc_now_naive()
        elif result.get("invalid_token"):
            notification.status = "failed_invalid_token"
        else:
            notification.status = "failed"
        await self.db.flush()

    async def send_push(
        self,
        user_id: str,
        title: str,
        message: str,
        data: dict | None = None,
        *,
        channel_id: str | None = None,
        notification: Notification | None = None,
    ) -> dict:
        """Send FCM to a user. Falls back to Celery enqueue when direct send is unavailable."""
        uid = UUID(str(user_id)) if user_id else None
        token = None
        if uid:
            user = await self.db.get(User, uid)
            token = user.fcm_token if user else None

        if not token:
            logger.info("fcm_push_skipped_no_token", user_id=str(user_id))
            if notification:
                notification.status = "skipped_no_token"
                await self.db.flush()
            return {"success": False, "error": "no_token"}

        result = fcm.send_to_token(
            token,
            title,
            message,
            data,
            channel_id=channel_id or "ride",
            analytics_label=(data or {}).get("event"),
        )
        await self._mark_delivery(notification, result)
        if result.get("invalid_token") and uid:
            await self._clear_invalid_token(user_id=uid)

        # Best-effort Celery enqueue for workers that may also process notifications.
        try:
            from app.tasks.celery_app import send_notification as celery_send

            celery_send.delay(str(user_id), title, message, data)
        except Exception:
            pass

        return result

    async def send_push_to_driver(
        self,
        driver_id: str | UUID,
        title: str,
        message: str,
        data: dict | None = None,
        *,
        channel_id: str | None = None,
        notification: Notification | None = None,
    ) -> dict:
        did = UUID(str(driver_id))
        driver = await self.db.get(Driver, did)
        token = driver.fcm_token if driver else None
        if not token:
            logger.info("fcm_push_skipped_no_token", driver_id=str(driver_id))
            if notification:
                notification.status = "skipped_no_token"
                await self.db.flush()
            return {"success": False, "error": "no_token"}

        result = fcm.send_to_token(
            token,
            title,
            message,
            data,
            channel_id=channel_id or "ride",
            analytics_label=(data or {}).get("event"),
        )
        await self._mark_delivery(notification, result)
        if result.get("invalid_token"):
            await self._clear_invalid_token(driver_id=did)
        return result

    async def notify_and_push(
        self,
        *,
        title: str,
        message: str,
        notification_type: str = "SYSTEM",
        user_id: UUID | None = None,
        driver_id: UUID | None = None,
        data: dict | None = None,
        event: str | None = None,
        ride_id: str | None = None,
        channel_id: str | None = None,
    ) -> Notification:
        payload = self._enrich_data(
            data,
            notification_type=notification_type,
            event=event,
            user_id=user_id,
            driver_id=driver_id,
            ride_id=ride_id,
        )
        notification = await self.create_in_app(
            title=title,
            message=message,
            notification_type=notification_type,
            user_id=user_id,
            driver_id=driver_id,
            data=payload,
        )
        channel = channel_id or _CHANNEL_BY_TYPE.get(notification_type.upper(), "admin")
        if user_id:
            await self.send_push(
                str(user_id),
                title,
                message,
                payload,
                channel_id=channel,
                notification=notification,
            )
        if driver_id:
            await self.send_push_to_driver(
                driver_id,
                title,
                message,
                payload,
                channel_id=channel,
                notification=notification,
            )
        return notification

    async def send_ride_notification(
        self,
        ride_id: str,
        event: str,
        user_id: UUID,
        driver_id: UUID | None = None,
    ):
        messages = {
            "ride_accepted": ("Ride Accepted", "Your driver is on the way!"),
            "driver_arrived": ("Driver Arrived", "Your driver has arrived at pickup location"),
            "ride_started": ("Ride Started", "Your ride has started. Enjoy your trip!"),
            "ride_completed": ("Ride Completed", "Your ride is complete. Please rate your driver."),
            "ride_cancelled": ("Ride Cancelled", "Your ride has been cancelled."),
        }
        title, message = messages.get(event, ("Ride Update", f"Ride status: {event}"))
        await self.notify_and_push(
            title=title,
            message=message,
            notification_type="RIDE",
            user_id=user_id,
            driver_id=None,
            event=event,
            ride_id=ride_id,
            data={"ride_id": ride_id, "event": event, "driver_id": str(driver_id) if driver_id else None},
            channel_id="ride",
        )

    async def notify_user_ride_accepted(self, ride, driver, vehicle=None) -> Notification:
        from app.utils.phone import format_phone_display

        driver_name = f"{driver.first_name} {driver.last_name}".strip() or "Driver"
        vehicle_number = vehicle.license_plate if vehicle else "—"
        phone = format_phone_display(driver.phone)
        start_code = ride.ride_otp or "----"
        fare = float(ride.estimated_fare or 0)

        message = (
            f"{driver_name} accepted your ride.\n"
            f"Vehicle: {vehicle_number}\n"
            f"Contact: {phone}\n"
            f"Start code: {start_code}\n"
            f"Share this code only when the driver arrives at pickup."
        )
        data = {
            "event": "ride_accepted",
            "ride_id": str(ride.id),
            "driver_id": str(driver.id),
            "driver_name": driver_name,
            "driver_phone": phone,
            "driver_rating": float(getattr(driver, "rating_avg", 0) or 0),
            "driver_photo_url": getattr(driver, "profile_photo", None),
            "vehicle_number": vehicle_number,
            "start_code": start_code,
            "pickup_address": ride.pickup_address,
            "dropoff_address": ride.dropoff_address,
            "estimated_fare": fare,
            "status": ride.status,
            "screen": "live_tracking",
        }
        if ride.vehicle_type:
            data["vehicle_type"] = {
                "id": str(ride.vehicle_type.id),
                "name": ride.vehicle_type.name,
                "slug": ride.vehicle_type.slug,
            }
            data["vehicle_type_slug"] = ride.vehicle_type.slug
            data["vehicle_type_name"] = ride.vehicle_type.name

        return await self.notify_and_push(
            title="Ride accepted!",
            message=message,
            notification_type="RIDE",
            user_id=ride.user_id,
            event="ride_accepted",
            ride_id=str(ride.id),
            data=data,
            channel_id="ride",
        )

    async def notify_driver_arrived(self, ride) -> Notification:
        return await self.notify_and_push(
            title="Driver Arrived",
            message="Your driver has arrived at the pickup location.",
            notification_type="RIDE",
            user_id=ride.user_id,
            event="driver_arrived",
            ride_id=str(ride.id),
            data={"status": ride.status},
            channel_id="ride",
        )

    async def notify_ride_started(self, ride) -> Notification:
        return await self.notify_and_push(
            title="Ride Started",
            message="Your ride has started. Enjoy your trip!",
            notification_type="RIDE",
            user_id=ride.user_id,
            event="ride_started",
            ride_id=str(ride.id),
            data={"status": ride.status},
            channel_id="ride",
        )

    async def notify_ride_completed(self, ride) -> Notification:
        fare = float(getattr(ride, "final_fare", None) or ride.estimated_fare or 0)
        return await self.notify_and_push(
            title="Ride Completed",
            message=f"Your ride is complete. Fare: ₹{fare:.0f}. Please rate your driver.",
            notification_type="RIDE",
            user_id=ride.user_id,
            event="ride_completed",
            ride_id=str(ride.id),
            data={"status": ride.status, "fare": fare},
            channel_id="ride",
        )

    async def notify_ride_cancelled(
        self,
        ride,
        *,
        reason: str = "Ride cancelled",
        notify_user: bool = True,
        notify_driver: bool = True,
    ) -> None:
        if notify_user and ride.user_id:
            await self.notify_and_push(
                title="Ride Cancelled",
                message=reason,
                notification_type="RIDE",
                user_id=ride.user_id,
                event="ride_cancelled",
                ride_id=str(ride.id),
                data={"reason": reason, "status": getattr(ride, "status", None)},
                channel_id="ride",
            )
        if notify_driver and ride.driver_id:
            await self.notify_and_push(
                title="Ride Cancelled",
                message=reason,
                notification_type="RIDE",
                driver_id=ride.driver_id,
                event="ride_cancelled",
                ride_id=str(ride.id),
                data={"reason": reason, "status": getattr(ride, "status", None)},
                channel_id="ride",
            )

    async def notify_driver_new_ride_request(
        self,
        driver_id: UUID,
        title: str,
        message: str,
        data: dict,
    ) -> Notification:
        return await self.notify_and_push(
            title=title,
            message=message,
            notification_type="RIDE",
            driver_id=driver_id,
            event="ride_request",
            ride_id=str(data.get("ride_id")) if data.get("ride_id") else None,
            data=data,
            channel_id="ride",
        )

    async def update_user_device_token(
        self,
        user: User,
        *,
        fcm_token: str,
        device_type: str = "android",
        device_id: str | None = None,
    ) -> list[str]:
        # Token uniqueness: clear from other users/drivers holding the same token
        await self.db.execute(
            update(User).where(User.fcm_token == fcm_token, User.id != user.id).values(fcm_token=None)
        )
        await self.db.execute(
            update(Driver).where(Driver.fcm_token == fcm_token).values(fcm_token=None)
        )
        user.fcm_token = fcm_token
        user.device_type = device_type
        if device_id:
            user.device_id = device_id
            user.last_login_device = device_id
        await self.db.flush()

        topics = [fcm.TOPIC_ALL_USERS, fcm.TOPIC_PROMOTION, fcm.TOPIC_NEWS]
        for topic in topics:
            fcm.subscribe_token(fcm_token, topic)
        return topics

    async def update_driver_device_token(
        self,
        driver: Driver,
        *,
        fcm_token: str,
        device_type: str = "android",
        device_id: str | None = None,
    ) -> list[str]:
        await self.db.execute(
            update(Driver).where(Driver.fcm_token == fcm_token, Driver.id != driver.id).values(fcm_token=None)
        )
        await self.db.execute(
            update(User).where(User.fcm_token == fcm_token).values(fcm_token=None)
        )
        driver.fcm_token = fcm_token
        driver.device_type = device_type
        if device_id:
            driver.device_id = device_id
            driver.last_login_device = device_id
        await self.db.flush()

        topics = [fcm.TOPIC_ALL_DRIVERS, fcm.TOPIC_PROMOTION, fcm.TOPIC_NEWS]
        for topic in topics:
            fcm.subscribe_token(fcm_token, topic)
        return topics

    async def list_for_user(
        self,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        filters = [Notification.user_id == user_id]
        if unread_only:
            filters.append(Notification.is_read.is_(False))
        base = select(Notification).where(*filters)
        total_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = int(total_result.scalar_one())
        result = await self.db.execute(
            base.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_for_driver(
        self,
        driver_id: UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Notification], int, int]:
        base = select(Notification).where(Notification.driver_id == driver_id)
        total_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = int(total_result.scalar_one())

        result = await self.db.execute(
            base.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(result.scalars().all())
        items = await self.filter_driver_notifications(items)
        unread_count = sum(1 for n in items if not n.is_read)
        return items, total, unread_count

    async def mark_driver_notification_read(self, notification_id: UUID, driver_id: UUID) -> Notification:
        result = await self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.driver_id == driver_id,
            )
        )
        notification = result.scalar_one_or_none()
        if not notification:
            raise NotFoundException("Notification not found")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = _utc_now_naive()
            await self.db.flush()
            await self.db.refresh(notification)
        return notification

    async def mark_all_driver_notifications_read(self, driver_id: UUID) -> int:
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.driver_id == driver_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=_utc_now_naive())
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def mark_user_notification_read(self, notification_id: UUID, user_id: UUID) -> Notification:
        result = await self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        notification = result.scalar_one_or_none()
        if not notification:
            raise NotFoundException("Notification not found")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = _utc_now_naive()
            await self.db.flush()
            await self.db.refresh(notification)
        return notification

    async def mark_all_user_notifications_read(self, user_id: UUID) -> int:
        result = await self.db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            .values(is_read=True, read_at=_utc_now_naive())
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def delete_notification(
        self,
        notification_id: UUID,
        *,
        user_id: UUID | None = None,
        driver_id: UUID | None = None,
    ) -> bool:
        filters = [Notification.id == notification_id]
        if user_id:
            filters.append(Notification.user_id == user_id)
        if driver_id:
            filters.append(Notification.driver_id == driver_id)
        result = await self.db.execute(select(Notification).where(*filters))
        notification = result.scalar_one_or_none()
        if not notification:
            raise NotFoundException("Notification not found")
        await self.db.delete(notification)
        await self.db.flush()
        return True

    def _is_ride_request_row(self, notification: Notification) -> bool:
        data = notification.data or {}
        return data.get("event") == "ride_request" and bool(data.get("ride_id"))

    async def _close_notification(self, notification: Notification, outcome: str) -> None:
        from sqlalchemy.orm.attributes import flag_modified

        data = dict(notification.data or {})
        data["outcome"] = outcome
        data["actions"] = []
        notification.data = data
        flag_modified(notification, "data")
        notification.is_read = True
        notification.read_at = _utc_now_naive()

    async def close_driver_ride_request(
        self,
        driver_id: UUID,
        ride_id: UUID,
        outcome: str,
    ) -> int:
        result = await self.db.execute(
            select(Notification).where(Notification.driver_id == driver_id)
        )
        updated = 0
        ride_key = str(ride_id)
        for notification in result.scalars().all():
            data = notification.data or {}
            if data.get("event") != "ride_request":
                continue
            if str(data.get("ride_id")) != ride_key:
                continue
            await self._close_notification(notification, outcome)
            updated += 1
        if updated:
            await self.db.flush()
        return updated

    async def close_all_ride_requests_for_ride(self, ride_id: UUID, outcome: str) -> int:
        result = await self.db.execute(
            select(Notification).where(Notification.notification_type == "RIDE")
        )
        updated = 0
        ride_key = str(ride_id)
        for notification in result.scalars().all():
            data = notification.data or {}
            if data.get("event") != "ride_request":
                continue
            if str(data.get("ride_id")) != ride_key:
                continue
            await self._close_notification(notification, outcome)
            updated += 1
        if updated:
            await self.db.flush()
        return updated

    async def filter_driver_notifications(self, items: list[Notification]) -> list[Notification]:
        """Drop stale ride-request rows; active requests are shown via /ride-requests popup only."""
        from app.core.constants import RideStatus
        from app.models import Ride

        ride_ids: list[UUID] = []
        for notification in items:
            if not self._is_ride_request_row(notification):
                continue
            data = notification.data or {}
            if data.get("outcome"):
                continue
            try:
                ride_ids.append(UUID(str(data["ride_id"])))
            except (TypeError, ValueError):
                continue

        statuses: dict[str, str] = {}
        created_map: dict[str, datetime] = {}
        if ride_ids:
            result = await self.db.execute(
                select(Ride.id, Ride.status, Ride.created_at).where(Ride.id.in_(ride_ids))
            )
            for rid, status, created_at in result.all():
                statuses[str(rid)] = status
                created_map[str(rid)] = created_at

        # Only surface ride requests that are still actively searching and fresh.
        from app.config.settings import settings

        seconds = max(30, int(settings.driver_request_timeout_seconds or 180))
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)

        filtered: list[Notification] = []
        for notification in items:
            if not self._is_ride_request_row(notification):
                filtered.append(notification)
                continue

            data = notification.data or {}
            if data.get("outcome"):
                continue

            ride_key = str(data.get("ride_id"))
            ride_status = statuses.get(ride_key)
            if ride_status is None or ride_status != RideStatus.SEARCHING_DRIVER.value:
                continue

            created = created_map.get(ride_key)
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff:
                    continue

            filtered.append(notification)

        return filtered
