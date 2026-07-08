"""Ride business logic — fare engine, lifecycle, timeline."""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from geopy.distance import geodesic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import ActorType, RideEventType, RideStatus
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.core.security import generate_otp
from app.maps.service import MapsService
from app.services.user_benefits_service import (
    apply_member_discount_to_fare,
    get_user_ride_discount_percent,
)
from app.models import PricingRule, Ride, VehicleType
from app.rides.crud import RideCRUD
from app.rides.schemas import (
    RideBookRequest,
    RideDetailResponse,
    RideEstimateRequest,
    RideEstimateResponse,
    RideResponse,
    RideTimelineEvent,
    VehicleTypeEstimate,
)


class FareEngine:
    """Configurable fare calculation — never duplicate in frontend."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_vehicle_types(self) -> list[VehicleType]:
        result = await self.db.execute(select(VehicleType).where(VehicleType.is_active.is_(True)))
        return list(result.scalars().all())

    async def get_pricing_rule(
        self, vehicle_type_id: UUID, city_id: Optional[str] = None
    ) -> Optional[PricingRule]:
        query = select(PricingRule).where(
            PricingRule.vehicle_type_id == vehicle_type_id,
            PricingRule.is_active.is_(True),
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    def calculate_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        return geodesic((lat1, lng1), (lat2, lng2)).kilometers

    @staticmethod
    def estimate_duration_min(distance_km: float, avg_speed_kmh: float = 30.0) -> float:
        return (distance_km / avg_speed_kmh) * 60

    def is_night_time(self, dt: Optional[datetime] = None) -> bool:
        dt = dt or datetime.now(timezone.utc)
        hour = dt.hour
        if settings.night_charge_start_hour > settings.night_charge_end_hour:
            return hour >= settings.night_charge_start_hour or hour < settings.night_charge_end_hour
        return settings.night_charge_start_hour <= hour < settings.night_charge_end_hour

    async def calculate_fare(
        self,
        vehicle_type: VehicleType,
        distance_km: float,
        duration_min: float,
        *,
        promo_discount: float = 0.0,
        surge_multiplier: float = 1.0,
        pricing_rule: Optional[PricingRule] = None,
        rental_hours: Optional[float] = None,
    ) -> dict:
        base_fare = pricing_rule.base_fare if pricing_rule else vehicle_type.base_fare

        if (vehicle_type.service_group or "ride") == "rental":
            included = float(getattr(vehicle_type, "included_hours", 4.0) or 4.0)
            per_hour = float(getattr(vehicle_type, "per_hour_rate", 0.0) or 0.0)
            hours = float(rental_hours if rental_hours is not None else included)
            extra_hours = max(0.0, hours - included)
            distance_fare = 0.0
            time_fare = extra_hours * per_hour
            subtotal = base_fare + time_fare
        else:
            per_km = pricing_rule.per_km_rate if pricing_rule else vehicle_type.per_km_rate
            per_min = pricing_rule.per_minute_rate if pricing_rule else vehicle_type.per_minute_rate

            distance_fare = distance_km * per_km
            time_fare = duration_min * per_min
            subtotal = base_fare + distance_fare + time_fare

        night_charges = 0.0
        if self.is_night_time():
            multiplier = pricing_rule.night_multiplier if pricing_rule else settings.night_charge_multiplier
            night_charges = subtotal * (multiplier - 1)

        peak_charges = subtotal * (surge_multiplier - 1) if surge_multiplier > 1 else 0.0
        # Rentals are hour-based packages; keep the displayed estimate aligned to the configured package rate.
        # (Ride flow can still apply fees/taxes if needed in the payment layer.)
        if (vehicle_type.service_group or "ride") == "rental":
            platform_fee = 0.0
            tax_amount = 0.0
        else:
            platform_fee = subtotal * (settings.platform_fee_percent / 100)
            tax_amount = (subtotal + platform_fee) * (settings.tax_percent / 100)
        total = subtotal + night_charges + peak_charges + platform_fee + tax_amount - promo_discount

        return {
            "base_fare": round(base_fare, 2),
            "distance_fare": round(distance_fare, 2),
            "time_fare": round(time_fare, 2),
            "night_charges": round(night_charges, 2),
            "peak_charges": round(peak_charges, 2),
            "platform_fee": round(platform_fee, 2),
            "tax_amount": round(tax_amount, 2),
            "promo_discount": round(promo_discount, 2),
            "estimated_fare": round(max(total, 0), 2),
        }

    async def estimate(
        self, data: RideEstimateRequest, user_id: Optional[UUID] = None
    ) -> RideEstimateResponse:
        distance_km = self.calculate_distance_km(
            data.pickup_lat, data.pickup_lng, data.dropoff_lat, data.dropoff_lng
        )
        duration_min = self.estimate_duration_min(distance_km)
        vehicle_types = await self.get_vehicle_types()

        if data.vehicle_type_id:
            vehicle_types = [vt for vt in vehicle_types if vt.id == data.vehicle_type_id]

        discount_pct = 0.0
        if user_id is not None:
            discount_pct = await get_user_ride_discount_percent(self.db, user_id)

        estimates: list[VehicleTypeEstimate] = []
        for vt in vehicle_types:
            rule = await self.get_pricing_rule(vt.id)
            fare = await self.calculate_fare(vt, distance_km, duration_min, pricing_rule=rule)
            if discount_pct > 0:
                fare = apply_member_discount_to_fare(fare, discount_pct)
            estimates.append(
                VehicleTypeEstimate(
                    vehicle_type_id=vt.id,
                    name=vt.name,
                    estimated_fare=fare["estimated_fare"],
                    original_fare=fare.get("original_fare"),
                    member_discount=fare.get("member_discount", 0),
                    discount_percent=fare.get("discount_percent", 0),
                    base_fare=fare["base_fare"],
                    distance_fare=fare["distance_fare"],
                    time_fare=fare["time_fare"],
                    night_charges=fare["night_charges"],
                    peak_charges=fare["peak_charges"],
                    tax_amount=fare["tax_amount"],
                    platform_fee=fare["platform_fee"],
                )
            )

        return RideEstimateResponse(
            distance_km=round(distance_km, 2),
            duration_min=round(duration_min, 2),
            vehicle_types=estimates,
            discount_percent=round(discount_pct, 2) if discount_pct > 0 else None,
        )


class RideService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.crud = RideCRUD(db)
        self.fare = FareEngine(db)
        self.maps = MapsService()

    async def _broadcast_realtime_update(self, ride: Ride, event: str = "ride_updated") -> None:
        # Local import to keep core ride logic decoupled from the API layer.
        from app.api.websocket.manager import manager

        payload = {
            "event": event,
            "ride_id": str(ride.id),
            "status": ride.status,
            "ride": RideService.to_response(ride).model_dump(),
        }

        # Subscribers who explicitly subscribed to this ride_id.
        await manager.broadcast_ride(str(ride.id), payload)

        # Also notify the participants directly, so the app can get updates without subscribing.
        await manager.send_personal(str(ride.user_id), payload)
        if ride.driver_id:
            await manager.send_personal(str(ride.driver_id), payload)

    async def _transition(
        self,
        ride: Ride,
        new_status: RideStatus,
        *,
        actor_type: ActorType,
        actor_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> Ride:
        ride.status = new_status.value
        await self.crud.add_event(
            ride_id=ride.id,
            event_type=new_status.value,
            actor_type=actor_type.value,
            actor_id=actor_id,
            metadata=metadata,
        )
        updated = await self.crud.update(ride)
        try:
            await self._broadcast_realtime_update(updated)
        except Exception:
            # Realtime is best-effort; don't block ride lifecycle on websocket.
            pass
        return updated

    async def book(self, user_id: UUID, data: RideBookRequest) -> Ride:
        await self.crud.cancel_orphaned_search_rides(user_id)
        if await self.crud.get_active_for_user(user_id):
            raise ValidationException("You already have an active ride")

        result = await self.db.execute(
            select(VehicleType).where(
                VehicleType.id == data.vehicle_type_id,
                VehicleType.is_active.is_(True),
            )
        )
        vehicle_type = result.scalar_one_or_none()
        if not vehicle_type:
            raise NotFoundException("Vehicle type not found")

        if (vehicle_type.service_group or "ride") == "rental":
            distance_km = 0.0
            hours = float(data.rental_hours) if data.rental_hours is not None else float(vehicle_type.included_hours)
            duration_min = hours * 60.0
        else:
            distance_km = self.fare.calculate_distance_km(
                data.pickup_lat, data.pickup_lng, data.dropoff_lat, data.dropoff_lng
            )
            duration_min = self.fare.estimate_duration_min(distance_km)
        rule = await self.fare.get_pricing_rule(data.vehicle_type_id)
        fare = await self.fare.calculate_fare(
            vehicle_type,
            distance_km,
            duration_min,
            pricing_rule=rule,
            rental_hours=data.rental_hours,
        )

        discount_pct = await get_user_ride_discount_percent(self.db, user_id)
        if discount_pct > 0:
            fare = apply_member_discount_to_fare(fare, discount_pct)

        promo_discount = 0.0
        promo_code_id = None
        if data.promo_code:
            from app.services.promo_service import resolve_promo_code

            promo, promo_discount = await resolve_promo_code(
                self.db,
                data.promo_code,
                order_amount=float(fare["estimated_fare"]),
            )
            promo_code_id = promo.id
            fare["estimated_fare"] = round(
                max(0.0, float(fare["estimated_fare"]) - promo_discount),
                2,
            )
            fare["promo_discount"] = promo_discount

        # Rental bookings are hour-based; don't block on external routing.
        if (vehicle_type.service_group or "ride") != "rental":
            await self.maps.get_route_between(data.pickup_address, data.dropoff_address)

        ride = Ride(
            user_id=user_id,
            vehicle_type_id=data.vehicle_type_id,
            status=RideStatus.REQUESTED.value,
            pickup_address=data.pickup_address,
            pickup_lat=data.pickup_lat,
            pickup_lng=data.pickup_lng,
            dropoff_address=data.dropoff_address,
            dropoff_lat=data.dropoff_lat,
            dropoff_lng=data.dropoff_lng,
            estimated_distance_km=round(distance_km, 2),
            estimated_duration_min=round(duration_min, 2),
            estimated_fare=fare["estimated_fare"],
            base_fare=fare["base_fare"],
            distance_fare=fare["distance_fare"],
            time_fare=fare["time_fare"],
            night_charges=fare["night_charges"],
            peak_charges=fare["peak_charges"],
            tax_amount=fare["tax_amount"],
            platform_fee=fare["platform_fee"],
            promo_discount=fare.get("promo_discount", 0),
            promo_code_id=promo_code_id,
            payment_method=data.payment_method,
            ride_otp=generate_otp(4),
            scheduled_at=data.scheduled_at,
        )
        ride = await self.crud.create(ride)
        if promo_code_id is not None:
            from app.coupons.models import PromoCode

            promo_row = await self.db.get(PromoCode, promo_code_id)
            if promo_row is not None:
                promo_row.used_count = int(promo_row.used_count or 0) + 1
        await self.crud.add_event(
            ride_id=ride.id,
            event_type=RideEventType.REQUESTED.value,
            actor_type=ActorType.USER.value,
            actor_id=user_id,
        )
        ride = await self._transition(
            ride, RideStatus.SEARCHING_DRIVER, actor_type=ActorType.SYSTEM
        )
        return ride

    async def get_ride(self, ride_id: UUID) -> Ride:
        ride = await self.crud.get_with_details(ride_id)
        if not ride:
            raise NotFoundException("Ride not found")
        return ride

    def _ensure_participant(self, ride: Ride, user_id: Optional[UUID], driver_id: Optional[UUID]) -> None:
        if user_id and ride.user_id == user_id:
            return
        if driver_id and ride.driver_id == driver_id:
            return
        raise ForbiddenException("Not allowed to access this ride")

    async def cancel(
        self,
        ride_id: UUID,
        *,
        cancelled_by: str,
        actor_id: UUID,
        reason: str,
    ) -> Ride:
        ride = await self.get_ride(ride_id)
        if ride.status in (RideStatus.COMPLETED.value, RideStatus.CANCELLED.value):
            raise ValidationException("Ride cannot be cancelled")
        ride.cancelled_at = datetime.now(timezone.utc)
        ride.cancelled_by = cancelled_by
        ride.cancellation_reason = reason
        actor = ActorType.USER if cancelled_by == "USER" else ActorType.DRIVER
        return await self._transition(
            ride,
            RideStatus.CANCELLED,
            actor_type=actor,
            actor_id=actor_id,
            metadata={"reason": reason},
        )

    async def accept(self, ride_id: UUID, driver_id: UUID, vehicle_id: UUID) -> Ride:
        ride = await self.get_ride(ride_id)
        if ride.status != RideStatus.SEARCHING_DRIVER.value:
            raise ValidationException("Ride is no longer available")
        if await self.crud.get_active_for_driver(driver_id):
            raise ValidationException("You already have an active ride")
        ride.driver_id = driver_id
        ride.vehicle_id = vehicle_id
        ride.accepted_at = datetime.now(timezone.utc)
        return await self._transition(
            ride,
            RideStatus.DRIVER_ASSIGNED,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )

    async def reject(self, ride_id: UUID, driver_id: UUID, reason: str = "") -> dict:
        ride = await self.get_ride(ride_id)
        if ride.status != RideStatus.SEARCHING_DRIVER.value:
            raise ValidationException("Ride is no longer available")
        await self.crud.add_event(
            ride_id=ride.id,
            event_type="DRIVER_REJECTED",
            actor_type=ActorType.DRIVER.value,
            actor_id=driver_id,
            metadata={"reason": reason} if reason else None,
        )
        return {"message": "Ride rejected", "ride_id": str(ride.id)}

    async def driver_arrived(self, ride_id: UUID, driver_id: UUID) -> Ride:
        ride = await self.get_ride(ride_id)
        self._ensure_participant(ride, None, driver_id)
        if ride.status == RideStatus.DRIVER_ARRIVED.value:
            return ride
        if ride.status != RideStatus.DRIVER_ASSIGNED.value:
            raise ValidationException("Invalid ride status")
        ride.arrived_at = datetime.now(timezone.utc)
        return await self._transition(
            ride,
            RideStatus.DRIVER_ARRIVED,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )

    async def verify_otp(self, ride_id: UUID, driver_id: UUID, otp: str) -> Ride:
        ride = await self.get_ride(ride_id)
        self._ensure_participant(ride, None, driver_id)
        if ride.status == RideStatus.DRIVER_ASSIGNED.value:
            ride = await self.driver_arrived(ride_id, driver_id)
        elif ride.status != RideStatus.DRIVER_ARRIVED.value:
            raise ValidationException("Invalid ride status")
        normalized_otp = str(otp).strip()
        if ride.ride_otp != normalized_otp:
            raise ValidationException("Invalid start code. Ask the passenger for the code shown in their app.")
        return await self._transition(
            ride,
            RideStatus.OTP_VERIFIED,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )

    async def start(self, ride_id: UUID, driver_id: UUID) -> Ride:
        ride = await self.get_ride(ride_id)
        self._ensure_participant(ride, None, driver_id)
        if ride.status != RideStatus.OTP_VERIFIED.value:
            raise ValidationException("OTP must be verified before starting the ride")
        ride.started_at = datetime.now(timezone.utc)
        ride = await self._transition(
            ride,
            RideStatus.STARTED,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )
        return await self._transition(
            ride,
            RideStatus.IN_PROGRESS,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )

    async def complete(self, ride_id: UUID, driver_id: UUID, actual_distance_km: Optional[float] = None) -> Ride:
        ride = await self.get_ride(ride_id)
        self._ensure_participant(ride, None, driver_id)
        if ride.status not in (RideStatus.STARTED.value, RideStatus.IN_PROGRESS.value):
            raise ValidationException("Invalid ride status")
        ride.actual_distance_km = actual_distance_km or ride.estimated_distance_km
        ride.actual_duration_min = ride.estimated_duration_min
        ride.final_fare = ride.estimated_fare
        ride.completed_at = datetime.now(timezone.utc)
        ride = await self._transition(
            ride,
            RideStatus.COMPLETED,
            actor_type=ActorType.DRIVER,
            actor_id=driver_id,
        )
        from app.services.ride_settlement_service import RideSettlementService

        return await RideSettlementService(self.db).settle_completed_ride(ride)

    @staticmethod
    def to_response(ride: Ride) -> RideResponse:
        return RideResponse.model_validate(ride)

    @staticmethod
    def to_detail(ride: Ride) -> RideDetailResponse:
        timeline = [
            RideTimelineEvent(
                event_type=e.event_type,
                actor_type=e.actor_type,
                actor_id=e.actor_id,
                created_at=e.created_at,
                metadata=e.event_metadata,
            )
            for e in (ride.events or [])
        ]
        data = RideDetailResponse.model_validate(ride)
        data.timeline = timeline
        if ride.driver:
            data.driver = {
                "id": str(ride.driver.id),
                "name": f"{ride.driver.first_name} {ride.driver.last_name}".strip(),
                "phone": ride.driver.phone,
                "rating_avg": ride.driver.rating_avg,
            }
        if ride.vehicle:
            data.vehicle = {
                "id": str(ride.vehicle.id),
                "model": ride.vehicle.model,
                "plate_number": ride.vehicle.plate_number,
            }
        return data

    # --- Legacy driver panel API compatibility ---

    async def accept_ride(self, ride_id: UUID, driver_id: UUID, vehicle_id: UUID) -> Ride:
        return await self.accept(ride_id, driver_id, vehicle_id)

    async def verify_otp_and_start(self, ride_id: UUID, otp: str) -> Ride:
        ride = await self.get_ride(ride_id)
        if not ride.driver_id:
            raise ValidationException("Driver not assigned")
        driver_id = ride.driver_id
        if ride.status in (RideStatus.STARTED.value, RideStatus.IN_PROGRESS.value):
            return ride
        if ride.status == RideStatus.OTP_VERIFIED.value:
            return await self.start(ride_id, driver_id)
        ride = await self.verify_otp(ride_id, driver_id, str(otp).strip())
        return await self.start(ride_id, driver_id)

    async def complete_ride(self, ride_id: UUID, actual_distance_km: Optional[float] = None) -> Ride:
        ride = await self.get_ride(ride_id)
        if not ride.driver_id:
            raise ValidationException("Driver not assigned")
        return await self.complete(ride_id, ride.driver_id, actual_distance_km)

    # --- Legacy user panel API compatibility ---

    async def create_ride(self, user_id: UUID, data: RideBookRequest) -> Ride:
        return await self.book(user_id, data)

    async def cancel_ride(self, ride_id: UUID, cancelled_by: str, reason: str) -> Ride:
        ride = await self.get_ride(ride_id)
        actor_id = ride.user_id if cancelled_by == "USER" else (ride.driver_id or ride.user_id)
        return await self.cancel(
            ride_id,
            cancelled_by=cancelled_by,
            actor_id=actor_id,
            reason=reason,
        )
