import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ACTIVE_RIDE_STATUSES, DRIVER_ACTIVE_RIDE_STATUSES, RideStatus
from app.models import Ride
from app.repositories.base import BaseRepository


class RideRepository(BaseRepository[Ride]):
    def __init__(self, db: AsyncSession):
        super().__init__(Ride, db)

    async def get_with_details(self, ride_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(
            select(Ride)
            .options(
                selectinload(Ride.user),
                selectinload(Ride.driver),
                selectinload(Ride.vehicle),
                selectinload(Ride.vehicle_type),
                selectinload(Ride.ratings),
            )
            .where(Ride.id == ride_id)
        )
        return result.scalar_one_or_none()

    async def get_user_rides(
        self, user_id: uuid.UUID, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> List[Ride]:
        from sqlalchemy.orm import selectinload

        query = select(Ride).options(selectinload(Ride.company)).where(Ride.user_id == user_id)
        if status:
            query = query.where(Ride.status == status)
        query = query.order_by(Ride.created_at.desc())
        query = self._paginate(query, page, page_size)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_driver_rides(
        self, driver_id: uuid.UUID, page: int = 1, page_size: int = 20, status: Optional[str] = None
    ) -> List[Ride]:
        query = select(Ride).where(Ride.driver_id == driver_id)
        if status:
            query = query.where(Ride.status == status)
        query = query.order_by(Ride.created_at.desc())
        query = self._paginate(query, page, page_size)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_active_ride_for_user(self, user_id: uuid.UUID) -> Optional[Ride]:
        result = await self.db.execute(
            select(Ride)
            .where(Ride.user_id == user_id, Ride.status.in_(ACTIVE_RIDE_STATUSES))
            .order_by(Ride.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_active_ride_for_driver(self, driver_id: uuid.UUID) -> Optional[Ride]:
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

    async def count_user_rides(self, user_id: uuid.UUID) -> int:
        return await self.count([Ride.user_id == user_id])
