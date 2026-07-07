"""Notification service - Push, Email, SMS, In-App."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models import Notification
from app.tasks.celery_app import send_notification

_DRIVER_TYPE_MAP = {
    "RIDE": "ride",
    "PROMO": "offer",
    "PAYMENT": "bonus",
    "SYSTEM": "system",
    "ADMIN": "system",
    "ADMIN_BROADCAST": "system",
    "CHAT": "system",
}


def _utc_now_naive() -> datetime:
    """notifications.read_at is TIMESTAMP WITHOUT TIME ZONE."""
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
    }


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_in_app(
        self,
        title: str,
        message: str,
        notification_type: str = "SYSTEM",
        user_id: UUID | None = None,
        driver_id: UUID | None = None,
        data: dict | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            driver_id=driver_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data=data,
        )
        self.db.add(notification)
        await self.db.flush()
        await self.db.refresh(notification)
        return notification

    async def send_push(self, user_id: str, title: str, message: str, data: dict | None = None):
        send_notification.delay(user_id, title, message, data)

    async def send_ride_notification(self, ride_id: str, event: str, user_id: UUID, driver_id: UUID | None = None):
        messages = {
            "ride_accepted": ("Ride Accepted", "Your driver is on the way!"),
            "driver_arrived": ("Driver Arrived", "Your driver has arrived at pickup location"),
            "ride_started": ("Ride Started", "Your ride has started. Enjoy your trip!"),
            "ride_completed": ("Ride Completed", "Your ride is complete. Please rate your driver."),
            "ride_cancelled": ("Ride Cancelled", "Your ride has been cancelled."),
        }
        title, message = messages.get(event, ("Ride Update", f"Ride status: {event}"))
        await self.create_in_app(title, message, "RIDE", user_id=user_id, data={"ride_id": ride_id, "event": event})
        await self.send_push(str(user_id), title, message, {"ride_id": ride_id})

    async def notify_user_ride_accepted(
        self,
        ride,
        driver,
        vehicle=None,
    ) -> Notification:
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
            "vehicle_number": vehicle_number,
            "start_code": start_code,
            "pickup_address": ride.pickup_address,
            "dropoff_address": ride.dropoff_address,
            "estimated_fare": fare,
            "status": ride.status,
        }
        if ride.vehicle_type:
            data["vehicle_type"] = {
                "id": str(ride.vehicle_type.id),
                "name": ride.vehicle_type.name,
                "slug": ride.vehicle_type.slug,
            }
            data["vehicle_type_slug"] = ride.vehicle_type.slug
            data["vehicle_type_name"] = ride.vehicle_type.name
        notification = await self.create_in_app(
            title="Ride accepted!",
            message=message,
            notification_type="RIDE",
            user_id=ride.user_id,
            data=data,
        )
        await self.send_push(
            str(ride.user_id),
            "Ride accepted!",
            f"{driver_name} is coming in {vehicle_number}. Start code: {start_code}",
            data,
        )
        return notification

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
        if ride_ids:
            result = await self.db.execute(
                select(Ride.id, Ride.status).where(Ride.id.in_(ride_ids))
            )
            statuses = {str(rid): status for rid, status in result.all()}

        filtered: list[Notification] = []
        for notification in items:
            if not self._is_ride_request_row(notification):
                filtered.append(notification)
                continue

            data = notification.data or {}
            if data.get("outcome"):
                continue

            ride_status = statuses.get(str(data.get("ride_id")))
            if ride_status is None or ride_status != RideStatus.SEARCHING_DRIVER.value:
                continue

            # Keep for real-time popup polling; alerts UI hides these client-side.
            filtered.append(notification)

        return filtered
