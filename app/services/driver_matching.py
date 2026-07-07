import json
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

    async def _persist_driver_location(
        self,
        driver_id: UUID,
        lat: float,
        lng: float,
        heading: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> None:
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
        redis = await self._get_redis()
        if not redis:
            logger.warning(
                "ride_request_redis_skipped",
                ride_id=str(ride_id),
                driver_count=len(driver_ids),
            )
            return

        try:
            key = f"{RIDE_REQUESTS_PREFIX}{ride_id}"
            for driver_id in driver_ids:
                await redis.sadd(key, driver_id)
                await redis.sadd(f"{DRIVER_PENDING_PREFIX}{driver_id}", str(ride_id))
                await redis.publish(
                    "ride_requests",
                    json.dumps({"ride_id": str(ride_id), "driver_id": driver_id}),
                )
            await redis.expire(key, settings.driver_request_timeout_seconds)
            for driver_id in driver_ids:
                await redis.expire(
                    f"{DRIVER_PENDING_PREFIX}{driver_id}",
                    settings.driver_request_timeout_seconds,
                )
            logger.info(
                "ride_request_sent_redis",
                ride_id=str(ride_id),
                driver_ids=driver_ids,
                timeout_seconds=settings.driver_request_timeout_seconds,
            )
        except Exception as exc:
            logger.error(
                "ride_request_redis_failed",
                ride_id=str(ride_id),
                driver_ids=driver_ids,
                error=str(exc),
            )

    async def get_pending_ride_ids(self, driver_id: UUID) -> List[UUID]:
        redis = await self._get_redis()
        pending: List[UUID] = []
        if redis:
            try:
                raw = await redis.smembers(f"{DRIVER_PENDING_PREFIX}{driver_id}")
                if raw:
                    pending = [UUID(ride_id) for ride_id in raw]
            except Exception:
                pass

        if pending:
            return pending

        return await self._open_ride_request_ids_from_db(driver_id)

    async def _open_ride_request_ids_from_db(self, driver_id: UUID) -> List[UUID]:
        from app.core.constants import RideStatus
        from app.models import Notification, Ride

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
            )
        )
        open_ids = {row[0] for row in status_result.all()}
        return [ride_id for ride_id in ride_ids if ride_id in open_ids]

    async def remember_pending_ride(self, driver_id: UUID, ride_id: UUID) -> None:
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
            await notif_service.create_in_app(
                title="New ride request",
                message=message,
                notification_type="RIDE",
                driver_id=driver.id,
                data=payload,
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
