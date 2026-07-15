import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.constants import DriverStatus, KYCStatus
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.database.redis import get_redis
from app.models import Driver, DriverLocation, Ride, Vehicle
from app.repositories.driver_repository import DriverRepository

logger = get_logger(__name__)

DRIVER_GEO_KEY = "drivers:geo"
DRIVER_META_PREFIX = "driver:meta:"
DRIVER_PENDING_PREFIX = "driver:pending:"
RIDE_REQUESTS_PREFIX = "ride:requests:"

# In-process fallback when Redis is down (single uvicorn worker).
_local_pending_by_driver: dict[str, set[str]] = {}
_local_drivers_by_ride: dict[str, set[str]] = {}
_last_redispatch_at: dict[str, float] = {}
_REDISPATCH_COOLDOWN_SEC = 8.0


class DriverMatchingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.driver_repo = DriverRepository(db)

    async def _get_redis(self):
        try:
            return await get_redis()
        except Exception as exc:
            logger.warning("redis_unavailable", error=str(exc))
            return None

    @staticmethod
    def _remember_local_pending(ride_id: str, driver_ids: List[str]) -> None:
        _local_drivers_by_ride.setdefault(ride_id, set()).update(driver_ids)
        for driver_id in driver_ids:
            _local_pending_by_driver.setdefault(driver_id, set()).add(ride_id)

    @staticmethod
    def _clear_local_pending_ride(ride_id: str) -> None:
        drivers = _local_drivers_by_ride.pop(ride_id, set())
        for driver_id in drivers:
            pending = _local_pending_by_driver.get(driver_id)
            if not pending:
                continue
            pending.discard(ride_id)
            if not pending:
                _local_pending_by_driver.pop(driver_id, None)

    @staticmethod
    def _clear_local_driver_pending(driver_id: str, ride_id: str) -> None:
        pending = _local_pending_by_driver.get(driver_id)
        if pending:
            pending.discard(ride_id)
            if not pending:
                _local_pending_by_driver.pop(driver_id, None)
        drivers = _local_drivers_by_ride.get(ride_id)
        if drivers:
            drivers.discard(driver_id)
            if not drivers:
                _local_drivers_by_ride.pop(ride_id, None)

    @staticmethod
    def searching_freshness_cutoff() -> datetime:
        """Only rides still within the active search window are offerable."""
        seconds = max(30, int(settings.driver_request_timeout_seconds or 180))
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)

    async def expire_stale_searching_rides(self) -> int:
        """Cancel SEARCHING_DRIVER rides older than the request timeout."""
        from app.core.constants import RideStatus

        cutoff = self.searching_freshness_cutoff()
        # DB may store naive timestamps — compare safely.
        cutoff_naive = cutoff.replace(tzinfo=None)
        result = await self.db.execute(
            select(Ride).where(
                Ride.status == RideStatus.SEARCHING_DRIVER.value,
                Ride.created_at < cutoff_naive,
            ).limit(50)
        )
        stale = list(result.scalars().all())
        if not stale:
            return 0

        now = datetime.now(timezone.utc)
        for ride in stale:
            ride.status = RideStatus.CANCELLED.value
            ride.cancelled_at = now
            ride.cancelled_by = "SYSTEM"
            ride.cancellation_reason = "Search timed out — no driver accepted"
            self._clear_local_pending_ride(str(ride.id))
        await self.db.flush()
        logger.info("stale_searching_rides_expired", count=len(stale))
        return len(stale)

    async def filter_fresh_searching_ride_ids(self, ride_ids: List[UUID]) -> List[UUID]:
        """Keep only rides that are currently SEARCHING and still fresh."""
        from app.core.constants import RideStatus

        if not ride_ids:
            return []
        cutoff = self.searching_freshness_cutoff().replace(tzinfo=None)
        result = await self.db.execute(
            select(Ride.id).where(
                Ride.id.in_(ride_ids),
                Ride.status == RideStatus.SEARCHING_DRIVER.value,
                Ride.created_at >= cutoff,
            )
        )
        fresh = {row[0] for row in result.all()}
        return [ride_id for ride_id in ride_ids if ride_id in fresh]

        result = await self.db.execute(
            select(DriverLocation).where(DriverLocation.driver_id == driver_id)
        )
        location = result.scalar_one_or_none()
        if location:
            location.lat = lat
            location.lng = lng
            location.heading = heading
            location.speed = speed
            location.is_available = True
        else:
            self.db.add(
                DriverLocation(
                    driver_id=driver_id,
                    lat=lat,
                    lng=lng,
                    heading=heading,
                    speed=speed,
                    is_available=True,
                )
            )
        await self.db.flush()

    async def update_driver_location(
        self,
        driver_id: UUID,
        lat: float,
        lng: float,
        heading: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> None:
        await self._persist_driver_location(driver_id, lat, lng, heading, speed)

        redis = await self._get_redis()
        if not redis:
            return

        try:
            await redis.geoadd(DRIVER_GEO_KEY, (lng, lat, str(driver_id)))
            await redis.hset(
                f"{DRIVER_META_PREFIX}{driver_id}",
                mapping={
                    "lat": str(lat),
                    "lng": str(lng),
                    "heading": str(heading or 0),
                    "speed": str(speed or 0),
                },
            )
            await redis.expire(f"{DRIVER_META_PREFIX}{driver_id}", 300)
        except Exception as exc:
            logger.warning(
                "driver_location_redis_update_failed",
                driver_id=str(driver_id),
                error=str(exc),
            )

    async def set_driver_online(self, driver_id: UUID, lat: float, lng: float, vehicle_type_id: str) -> None:
        await self.update_driver_location(driver_id, lat, lng)

        redis = await self._get_redis()
        if not redis:
            return

        try:
            await redis.sadd("drivers:online", str(driver_id))
            await redis.hset(
                f"{DRIVER_META_PREFIX}{driver_id}",
                mapping={"vehicle_type_id": vehicle_type_id, "available": "1"},
            )
        except Exception as exc:
            logger.warning(
                "driver_online_redis_update_failed",
                driver_id=str(driver_id),
                error=str(exc),
            )

    async def set_driver_offline(self, driver_id: UUID) -> None:
        logger.info("driver_going_offline", driver_id=str(driver_id))
        redis = await self._get_redis()
        if not redis:
            return

        try:
            await redis.zrem(DRIVER_GEO_KEY, str(driver_id))
            await redis.srem("drivers:online", str(driver_id))
            await redis.delete(f"{DRIVER_META_PREFIX}{driver_id}")
        except Exception as exc:
            logger.warning(
                "driver_offline_redis_update_failed",
                driver_id=str(driver_id),
                error=str(exc),
            )

    async def _online_drivers_from_db(
        self,
        vehicle_type_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[dict]:
        query = select(Driver).where(
            Driver.status == DriverStatus.ONLINE.value,
            Driver.kyc_status == KYCStatus.APPROVED.value,
            Driver.is_active.is_(True),
            Driver.is_deleted.is_(False),
        )
        if vehicle_type_id:
            query = (
                query.join(Vehicle, Vehicle.driver_id == Driver.id)
                .where(Vehicle.vehicle_type_id == UUID(vehicle_type_id))
                .distinct()
            )
        result = await self.db.execute(query.limit(limit))
        drivers = list(result.scalars().unique().all())

        items: List[dict] = []
        for driver in drivers:
            loc_result = await self.db.execute(
                select(DriverLocation).where(DriverLocation.driver_id == driver.id)
            )
            location = loc_result.scalar_one_or_none()
            items.append({
                "driver_id": str(driver.id),
                "distance_km": 0.0,
                "lat": location.lat if location else 0.0,
                "lng": location.lng if location else 0.0,
                "name": f"{driver.first_name} {driver.last_name}",
                "rating": driver.rating_avg,
            })
        return items

    async def find_nearby_drivers(
        self,
        lat: float,
        lng: float,
        vehicle_type_id: Optional[str] = None,
        radius_km: Optional[float] = None,
        limit: int = 10,
    ) -> List[dict]:
        logger.info(
            "find_nearby_drivers_started",
            lat=lat,
            lng=lng,
            vehicle_type_id=vehicle_type_id,
            radius_km=radius_km or settings.driver_search_radius_km,
            limit=limit,
        )
        redis = await self._get_redis()
        if not redis:
            drivers = await self._online_drivers_from_db(vehicle_type_id, limit)
            logger.info(
                "find_nearby_drivers_db_fallback",
                source="database",
                count=len(drivers),
                vehicle_type_id=vehicle_type_id,
            )
            return drivers

        radius = radius_km or settings.driver_search_radius_km
        radius_m = radius * 1000

        try:
            results = await redis.geosearch(
                DRIVER_GEO_KEY,
                longitude=lng,
                latitude=lat,
                radius=radius_m,
                unit="m",
                sort="ASC",
                count=limit * 2,
                withdist=True,
            )
        except Exception as exc:
            logger.warning(
                "find_nearby_drivers_redis_geosearch_failed",
                error=str(exc),
            )
            drivers = await self._online_drivers_from_db(vehicle_type_id, limit)
            logger.info(
                "find_nearby_drivers_db_fallback",
                source="database",
                count=len(drivers),
                vehicle_type_id=vehicle_type_id,
            )
            return drivers

        drivers = []
        for driver_id, distance in results:
            try:
                meta = await redis.hgetall(f"{DRIVER_META_PREFIX}{driver_id}")
            except Exception:
                continue
            if not meta or meta.get("available") != "1":
                continue
            if vehicle_type_id and meta.get("vehicle_type_id") != vehicle_type_id:
                continue

            driver = await self.driver_repo.get_by_id(UUID(driver_id))
            if not driver or driver.status != DriverStatus.ONLINE.value:
                continue
            if driver.kyc_status != KYCStatus.APPROVED.value:
                continue

            drivers.append({
                "driver_id": driver_id,
                "distance_km": round(float(distance) / 1000, 2),
                "lat": float(meta.get("lat", 0)),
                "lng": float(meta.get("lng", 0)),
                "name": f"{driver.first_name} {driver.last_name}",
                "rating": driver.rating_avg,
            })

            if len(drivers) >= limit:
                break

        if not drivers:
            drivers = await self._online_drivers_from_db(vehicle_type_id, limit)
            logger.info(
                "find_nearby_drivers_db_fallback",
                source="database",
                count=len(drivers),
                vehicle_type_id=vehicle_type_id,
                reason="no_redis_geo_matches",
            )
        else:
            logger.info(
                "find_nearby_drivers_completed",
                source="redis",
                count=len(drivers),
                vehicle_type_id=vehicle_type_id,
            )

        return drivers

    async def send_ride_request(self, ride_id: UUID, driver_ids: List[str]) -> None:
        ride_key = str(ride_id)
        self._remember_local_pending(ride_key, driver_ids)

        redis = await self._get_redis()
        if not redis:
            logger.warning(
                "ride_request_redis_skipped",
                ride_id=ride_key,
                driver_count=len(driver_ids),
                fallback="local_memory",
            )
            return

        try:
            key = f"{RIDE_REQUESTS_PREFIX}{ride_id}"
            for driver_id in driver_ids:
                await redis.sadd(key, driver_id)
                await redis.sadd(f"{DRIVER_PENDING_PREFIX}{driver_id}", ride_key)
                await redis.publish(
                    "ride_requests",
                    json.dumps({"ride_id": ride_key, "driver_id": driver_id}),
                )
            await redis.expire(key, settings.driver_request_timeout_seconds)
            for driver_id in driver_ids:
                await redis.expire(
                    f"{DRIVER_PENDING_PREFIX}{driver_id}",
                    settings.driver_request_timeout_seconds,
                )
            logger.info(
                "ride_request_sent_redis",
                ride_id=ride_key,
                driver_ids=driver_ids,
                timeout_seconds=settings.driver_request_timeout_seconds,
            )
        except Exception as exc:
            logger.error(
                "ride_request_redis_failed",
                ride_id=ride_key,
                driver_ids=driver_ids,
                error=str(exc),
                fallback="local_memory",
            )

    async def get_pending_ride_ids(self, driver_id: UUID) -> List[UUID]:
        await self.expire_stale_searching_rides()

        pending: List[UUID] = []
        redis = await self._get_redis()
        if redis:
            try:
                raw = await redis.smembers(f"{DRIVER_PENDING_PREFIX}{driver_id}")
                if raw:
                    pending = [UUID(ride_id) for ride_id in raw]
            except Exception:
                pass

        if not pending:
            local = _local_pending_by_driver.get(str(driver_id), set())
            pending = [UUID(ride_id) for ride_id in local]

        if pending:
            fresh = await self.filter_fresh_searching_ride_ids(pending)
            # Drop stale IDs from local memory so they never resurface.
            for ride_id in pending:
                if ride_id not in fresh:
                    self._clear_local_driver_pending(str(driver_id), str(ride_id))
            return fresh

        return await self._open_ride_request_ids_from_db(driver_id)

    async def _open_ride_request_ids_from_db(self, driver_id: UUID) -> List[UUID]:
        from app.core.constants import RideStatus
        from app.models import Notification, Ride

        cutoff = self.searching_freshness_cutoff().replace(tzinfo=None)
        result = await self.db.execute(
            select(Notification)
            .where(Notification.driver_id == driver_id)
            .order_by(Notification.created_at.desc())
            .limit(30)
        )
        ride_ids: List[UUID] = []
        seen: set[UUID] = set()
        for notification in result.scalars().all():
            data = notification.data or {}
            if data.get("event") != "ride_request":
                continue
            if data.get("outcome"):
                continue
            try:
                ride_id = UUID(str(data["ride_id"]))
            except (TypeError, ValueError):
                continue
            if ride_id in seen:
                continue
            seen.add(ride_id)
            ride_ids.append(ride_id)

        if not ride_ids:
            return []

        status_result = await self.db.execute(
            select(Ride.id).where(
                Ride.id.in_(ride_ids),
                Ride.status == RideStatus.SEARCHING_DRIVER.value,
                Ride.created_at >= cutoff,
            )
        )
        open_ids = {row[0] for row in status_result.all()}
        return [ride_id for ride_id in ride_ids if ride_id in open_ids]

    async def remember_pending_ride(self, driver_id: UUID, ride_id: UUID) -> None:
        self._remember_local_pending(str(ride_id), [str(driver_id)])
        redis = await self._get_redis()
        if not redis:
            return
        try:
            key = f"{DRIVER_PENDING_PREFIX}{driver_id}"
            await redis.sadd(key, str(ride_id))
            await redis.sadd(f"{RIDE_REQUESTS_PREFIX}{ride_id}", str(driver_id))
            await redis.expire(key, settings.driver_request_timeout_seconds)
            await redis.expire(
                f"{RIDE_REQUESTS_PREFIX}{ride_id}",
                settings.driver_request_timeout_seconds,
            )
        except Exception:
            pass

    async def clear_driver_pending(self, driver_id: UUID, ride_id: UUID) -> None:
        self._clear_local_driver_pending(str(driver_id), str(ride_id))
        redis = await self._get_redis()
        if not redis:
            return

        try:
            await redis.srem(f"{DRIVER_PENDING_PREFIX}{driver_id}", str(ride_id))
            await redis.srem(f"{RIDE_REQUESTS_PREFIX}{ride_id}", str(driver_id))
        except Exception:
            pass

    async def clear_ride_requests(self, ride_id: UUID) -> None:
        """Remove a ride from all driver pending sets when cancelled or completed."""
        self._clear_local_pending_ride(str(ride_id))
        redis = await self._get_redis()
        if not redis:
            return

        try:
            key = f"{RIDE_REQUESTS_PREFIX}{ride_id}"
            driver_ids = await redis.smembers(key)
            for driver_id in driver_ids:
                await redis.srem(f"{DRIVER_PENDING_PREFIX}{driver_id}", str(ride_id))
            await redis.delete(key)
        except Exception:
            pass

    async def _online_drivers_for_ride(self, ride: Ride) -> List[Driver]:
        matched = await self.db.execute(
            select(Driver)
            .join(Vehicle, Vehicle.driver_id == Driver.id)
            .where(
                Driver.status == DriverStatus.ONLINE.value,
                Driver.kyc_status == KYCStatus.APPROVED.value,
                Driver.is_active.is_(True),
                Driver.is_deleted.is_(False),
                Vehicle.vehicle_type_id == ride.vehicle_type_id,
            )
            .distinct()
        )
        drivers = list(matched.scalars().unique().all())
        if drivers:
            return drivers

        fallback = await self.db.execute(
            select(Driver).where(
                Driver.status == DriverStatus.ONLINE.value,
                Driver.kyc_status == KYCStatus.APPROVED.value,
                Driver.is_active.is_(True),
                Driver.is_deleted.is_(False),
            )
        )
        return list(fallback.scalars().all())

    async def list_open_searching_rides_for_driver(
        self, driver: Driver, *, limit: int = 5
    ) -> List[Ride]:
        """Only present (fresh) SEARCHING_DRIVER rides — never past/stale searches."""
        from app.core.constants import RideStatus
        from sqlalchemy.orm import selectinload

        await self.expire_stale_searching_rides()
        cutoff = self.searching_freshness_cutoff().replace(tzinfo=None)

        vehicle_type_ids: list[UUID] = []
        vt_result = await self.db.execute(
            select(Vehicle.vehicle_type_id).where(
                Vehicle.driver_id == driver.id,
                Vehicle.is_deleted.is_(False),
            )
        )
        vehicle_type_ids = [row[0] for row in vt_result.all() if row[0]]

        query = (
            select(Ride)
            .options(selectinload(Ride.user))
            .where(
                Ride.status == RideStatus.SEARCHING_DRIVER.value,
                Ride.created_at >= cutoff,
            )
            .order_by(Ride.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(query)
        rides = list(result.scalars().unique().all())

        if vehicle_type_ids:
            matched = [r for r in rides if r.vehicle_type_id in vehicle_type_ids]
            if matched:
                return matched
        return rides

    async def dispatch_ride_to_online_drivers(self, ride: Ride, ws_manager=None) -> int:
        from app.notifications.service import NotificationService
        from sqlalchemy.orm import selectinload

        logger.info(
            "driver_dispatch_started",
            ride_id=str(ride.id),
            vehicle_type_id=str(ride.vehicle_type_id),
            pickup_lat=ride.pickup_lat,
            pickup_lng=ride.pickup_lng,
            status=ride.status,
        )

        result = await self.db.execute(
            select(Ride).options(selectinload(Ride.user)).where(Ride.id == ride.id)
        )
        ride = result.scalar_one()

        drivers = await self._online_drivers_for_ride(ride)
        if not drivers:
            logger.warning(
                "driver_dispatch_no_drivers",
                ride_id=str(ride.id),
                vehicle_type_id=str(ride.vehicle_type_id),
                hint="Driver must be ONLINE, KYC APPROVED, and have matching vehicle type",
            )
            return 0

        driver_ids = [str(driver.id) for driver in drivers]
        logger.info(
            "driver_dispatch_candidates",
            ride_id=str(ride.id),
            driver_count=len(drivers),
            driver_ids=driver_ids,
        )
        try:
            await self.send_ride_request(ride.id, driver_ids)
        except Exception as exc:
            logger.error(
                "driver_dispatch_redis_enqueue_failed",
                ride_id=str(ride.id),
                driver_ids=driver_ids,
                error=str(exc),
            )

        passenger_name = (
            f"{ride.user.first_name} {ride.user.last_name}".strip()
            if ride.user
            else "Passenger"
        )
        fare = float(ride.estimated_fare or 0)
        distance = float(ride.estimated_distance_km or 0)
        duration = float(ride.estimated_duration_min or 0)

        notif_service = NotificationService(self.db)
        payload = {
            "event": "ride_request",
            "ride_id": str(ride.id),
            "pickup_address": ride.pickup_address,
            "dropoff_address": ride.dropoff_address,
            "pickup_lat": ride.pickup_lat,
            "pickup_lng": ride.pickup_lng,
            "dropoff_lat": ride.dropoff_lat,
            "dropoff_lng": ride.dropoff_lng,
            "estimated_fare": fare,
            "estimated_distance_km": distance,
            "estimated_duration_min": duration,
            "payment_method": ride.payment_method,
            "passenger_name": passenger_name,
            "passenger_phone": ride.user.phone if ride.user else None,
            "status": ride.status,
            "actions": ["accept", "reject"],
        }
        message = (
            f"{passenger_name}\n"
            f"From: {ride.pickup_address}\n"
            f"To: {ride.dropoff_address}\n"
            f"Fare: ₹{fare:.0f} · {distance:.1f} km · {duration:.0f} min"
        )

        for driver in drivers:
            await notif_service.notify_driver_new_ride_request(
                driver.id,
                "New ride request",
                message,
                payload,
            )
            if ws_manager:
                await ws_manager.send_personal(str(driver.id), payload)

        logger.info(
            "driver_dispatch_completed",
            ride_id=str(ride.id),
            drivers_notified=len(drivers),
            driver_ids=driver_ids,
            websocket=ws_manager is not None,
        )
        return len(drivers)

    async def rediscover_searching_ride(self, ride: Ride, ws_manager=None) -> int:
        """Re-ping online drivers for a stuck SEARCHING_DRIVER ride (throttled).

        Uses local/Redis pending + websocket only — avoids spamming new DB
        notifications every few seconds while the user is searching.
        """
        from app.core.constants import RideStatus
        from sqlalchemy.orm import selectinload

        if ride.status != RideStatus.SEARCHING_DRIVER.value:
            return 0

        cutoff = self.searching_freshness_cutoff()
        created = ride.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                logger.info(
                    "driver_redispatch_skipped_stale",
                    ride_id=str(ride.id),
                )
                return 0

        ride_key = str(ride.id)
        now = time.monotonic()
        last = _last_redispatch_at.get(ride_key, 0.0)
        if now - last < _REDISPATCH_COOLDOWN_SEC:
            return 0
        _last_redispatch_at[ride_key] = now

        result = await self.db.execute(
            select(Ride).options(selectinload(Ride.user)).where(Ride.id == ride.id)
        )
        ride = result.scalar_one()
        drivers = await self._online_drivers_for_ride(ride)
        if not drivers:
            logger.warning(
                "driver_redispatch_no_drivers",
                ride_id=ride_key,
            )
            return 0

        driver_ids = [str(d.id) for d in drivers]
        await self.send_ride_request(ride.id, driver_ids)

        passenger_name = (
            f"{ride.user.first_name} {ride.user.last_name}".strip()
            if ride.user
            else "Passenger"
        )
        payload = {
            "event": "ride_request",
            "ride_id": str(ride.id),
            "pickup_address": ride.pickup_address,
            "dropoff_address": ride.dropoff_address,
            "pickup_lat": ride.pickup_lat,
            "pickup_lng": ride.pickup_lng,
            "dropoff_lat": ride.dropoff_lat,
            "dropoff_lng": ride.dropoff_lng,
            "estimated_fare": float(ride.estimated_fare or 0),
            "estimated_distance_km": float(ride.estimated_distance_km or 0),
            "estimated_duration_min": float(ride.estimated_duration_min or 0),
            "payment_method": ride.payment_method,
            "passenger_name": passenger_name,
            "passenger_phone": ride.user.phone if ride.user else None,
            "status": ride.status,
            "actions": ["accept", "reject"],
        }
        if ws_manager:
            for driver in drivers:
                await ws_manager.send_personal(str(driver.id), payload)

        logger.info(
            "driver_redispatch_completed",
            ride_id=ride_key,
            drivers_notified=len(drivers),
            driver_ids=driver_ids,
        )
        return len(drivers)

    async def ensure_driver_online(self, driver: Driver, lat: float, lng: float) -> None:
        vehicle_result = await self.db.execute(
            select(Vehicle).where(Vehicle.driver_id == driver.id).limit(1)
        )
        vehicle = vehicle_result.scalar_one_or_none()
        vehicle_type_id = str(vehicle.vehicle_type_id) if vehicle else ""
        logger.info(
            "driver_going_online",
            driver_id=str(driver.id),
            lat=lat,
            lng=lng,
            vehicle_type_id=vehicle_type_id,
        )
        await self.set_driver_online(driver.id, lat, lng, vehicle_type_id)

    async def driver_default_location(self, driver_id: UUID) -> tuple[float, float]:
        result = await self.db.execute(
            select(DriverLocation).where(DriverLocation.driver_id == driver_id)
        )
        location = result.scalar_one_or_none()
        if location:
            return location.lat, location.lng
        return 28.6139, 77.2090
