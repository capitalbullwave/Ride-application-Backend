"""Ride data access layer."""
import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ACTIVE_RIDE_STATUSES, DRIVER_ACTIVE_RIDE_STATUSES
from app.rides.models import Ride, RideEvent


class RideCRUD:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, ride: Ride) -> Ride:
        self.db.add(ride)
        await self.db.flush()
        await self.db.refresh(ride)
        return ride

    async def update(self, ride: Ride) -> Ride:
        await self.db.flush()
        await self.db.refresh(ride)
        return ride

    async def get_by_id(self, ride_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(select(Ride).where(Ride.id == ride_id))
        return result.scalar_one_or_none()

    async def get_with_details(self, ride_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(
            select(Ride)
            .options(
                selectinload(Ride.user),
                selectinload(Ride.driver),
                selectinload(Ride.vehicle),
                selectinload(Ride.vehicle_type),
                selectinload(Ride.ratings),
                selectinload(Ride.events),
            )
            .where(Ride.id == ride_id)
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(
            select(Ride)
            .where(Ride.user_id == user_id, Ride.status.in_(ACTIVE_RIDE_STATUSES))
            .order_by(Ride.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_active_for_driver(self, driver_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(
            select(Ride)
            .where(
                Ride.driver_id == driver_id,
                Ride.status.in_(DRIVER_ACTIVE_RIDE_STATUSES),
            )
            .order_by(Ride.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def cancel_orphaned_search_rides(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Cancel stale driver-search rides so a new booking can proceed."""
        from datetime import datetime, timezone

        from app.core.constants import RideStatus

        result = await self.db.execute(
            select(Ride).where(
                Ride.user_id == user_id,
                Ride.status.in_(
                    (RideStatus.REQUESTED.value, RideStatus.SEARCHING_DRIVER.value)
                ),
            )
        )
        rides = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        cancelled_ids: list[uuid.UUID] = []
        for ride in rides:
            ride.status = RideStatus.CANCELLED.value
            ride.cancelled_at = now
            ride.cancelled_by = "USER"
            ride.cancellation_reason = "Superseded by new booking"
            await self.update(ride)
            cancelled_ids.append(ride.id)
        return cancelled_ids

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
    ) -> List[Ride]:
        query = select(Ride).where(Ride.user_id == user_id)
        if status:
            query = query.where(Ride.status == status)
        query = query.order_by(Ride.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_for_driver(
        self,
        driver_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
    ) -> List[Ride]:
        query = select(Ride).where(Ride.driver_id == driver_id)
        if status:
            query = query.where(Ride.status == status)
        query = query.order_by(Ride.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_searching(self, limit: int = 20) -> List[Ride]:
        from app.core.constants import RideStatus

        result = await self.db.execute(
            select(Ride)
            .where(
                Ride.status.in_([
                    RideStatus.REQUESTED.value,
                    RideStatus.SEARCHING_DRIVER.value,
                ])
            )
            .order_by(Ride.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_event(
        self,
        *,
        ride_id: uuid.UUID,
        event_type: str,
        actor_type: str,
        actor_id: Optional[uuid.UUID] = None,
        metadata: Optional[dict] = None,
    ) -> RideEvent:
        event = RideEvent(
            ride_id=ride_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            event_metadata=metadata,
        )
        self.db.add(event)
        await self.db.flush()
        return event
