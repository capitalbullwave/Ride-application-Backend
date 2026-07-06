"""Ride rating helpers — user ↔ driver reviews."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RaterType, RideStatus
from app.core.exceptions import ForbiddenException, ValidationException
from app.models import Driver, Rating, Ride, User
from app.rides.service import RideService


class RatingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_completed_ride(self, ride_id: UUID) -> Ride:
        ride = await RideService(self.db).get_ride(ride_id)
        if ride.status != RideStatus.COMPLETED.value:
            raise ValidationException("Ride must be completed before rating")
        return ride

    async def _existing(self, ride_id: UUID, rater_type: str) -> Rating | None:
        result = await self.db.execute(
            select(Rating).where(
                Rating.ride_id == ride_id,
                Rating.rater_type == rater_type,
            )
        )
        return result.scalar_one_or_none()

    async def rate_driver(
        self,
        ride_id: UUID,
        user: User,
        rating: int,
        comment: str | None = None,
    ) -> dict:
        ride = await self._get_completed_ride(ride_id)
        if ride.user_id != user.id:
            raise ForbiddenException("Access denied")
        if not ride.driver_id:
            raise ValidationException("No driver assigned to this ride")
        if await self._existing(ride.id, RaterType.USER.value):
            raise ValidationException("You already rated this ride")

        row = Rating(
            ride_id=ride.id,
            user_id=user.id,
            driver_id=ride.driver_id,
            rater_type=RaterType.USER.value,
            rating=rating,
            comment=comment,
        )
        self.db.add(row)
        await self.db.flush()

        driver = await self.db.get(Driver, ride.driver_id)
        if driver:
            avg_result = await self.db.execute(
                select(func.avg(Rating.rating)).where(
                    Rating.driver_id == ride.driver_id,
                    Rating.rater_type == RaterType.USER.value,
                )
            )
            avg = avg_result.scalar_one()
            driver.rating_avg = float(avg) if avg is not None else float(rating)

        await self.db.flush()
        return {"ride_id": str(ride.id), "rating": rating, "target": "driver"}

    async def rate_user(
        self,
        ride_id: UUID,
        driver: Driver,
        rating: int,
        comment: str | None = None,
    ) -> dict:
        ride = await self._get_completed_ride(ride_id)
        if ride.driver_id != driver.id:
            raise ForbiddenException("Access denied")
        if await self._existing(ride.id, RaterType.DRIVER.value):
            raise ValidationException("You already rated this passenger")

        row = Rating(
            ride_id=ride.id,
            user_id=ride.user_id,
            driver_id=driver.id,
            rater_type=RaterType.DRIVER.value,
            rating=rating,
            comment=comment,
        )
        self.db.add(row)
        await self.db.flush()

        passenger = await self.db.get(User, ride.user_id)
        if passenger:
            avg_result = await self.db.execute(
                select(func.avg(Rating.rating)).where(
                    Rating.user_id == ride.user_id,
                    Rating.rater_type == RaterType.DRIVER.value,
                )
            )
            avg = avg_result.scalar_one()
            passenger.rating_avg = float(avg) if avg is not None else float(rating)

        await self.db.flush()
        return {"ride_id": str(ride.id), "rating": rating, "target": "user"}
