"""Aggregated driver dashboard statistics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RideStatus
from app.drivers.models import Driver
from app.models import Ride, RideEvent
from app.services.driver_wallet_service import DriverWalletService


def _start_of_period_utc(period: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        return (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


class DriverDashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def count_completed_trips(self, driver_id) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Ride)
            .where(
                Ride.driver_id == driver_id,
                Ride.status == RideStatus.COMPLETED.value,
            )
        )
        return int(result.scalar_one() or 0)

    async def get_stats(self, driver: Driver) -> dict:
        wallet = await DriverWalletService(self.db).get_or_create(driver.id)
        today_start = _start_of_period_utc("daily")

        today_earnings_result = await self.db.execute(
            select(func.coalesce(func.sum(Ride.driver_earning), 0)).where(
                Ride.driver_id == driver.id,
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= today_start,
                Ride.driver_earning.isnot(None),
            )
        )
        today_earnings = float(today_earnings_result.scalar_one() or 0)

        completed_trips = await self.count_completed_trips(driver.id)

        today_trips_result = await self.db.execute(
            select(func.count())
            .select_from(Ride)
            .where(
                Ride.driver_id == driver.id,
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= today_start,
            )
        )
        today_trips = int(today_trips_result.scalar_one() or 0)

        accepted_result = await self.db.execute(
            select(func.count())
            .select_from(Ride)
            .where(Ride.driver_id == driver.id)
        )
        accepted = int(accepted_result.scalar_one() or 0)

        rejected_result = await self.db.execute(
            select(func.count())
            .select_from(RideEvent)
            .where(
                RideEvent.event_type == "DRIVER_REJECTED",
                RideEvent.actor_id == driver.id,
            )
        )
        rejected = int(rejected_result.scalar_one() or 0)

        total_offers = accepted + rejected
        if total_offers > 0:
            acceptance_rate = round((accepted / total_offers) * 100, 1)
        else:
            acceptance_rate = 100.0

        return {
            "today_earnings": today_earnings,
            "wallet_balance": float(wallet.available_balance or 0),
            "completed_trips": completed_trips,
            "today_trips": today_trips,
            "rating": round(float(driver.rating_avg or 0), 1),
            "acceptance_rate": acceptance_rate,
        }

    async def earnings_for_period(self, driver: Driver, period: str) -> dict:
        period_start = _start_of_period_utc(period)

        earnings_query = select(func.coalesce(func.sum(Ride.driver_earning), 0)).where(
            Ride.driver_id == driver.id,
            Ride.status == RideStatus.COMPLETED.value,
            Ride.driver_earning.isnot(None),
        )
        if period_start is not None:
            earnings_query = earnings_query.where(Ride.completed_at >= period_start)

        earnings_sum = await self.db.execute(earnings_query)
        period_earnings = float(earnings_sum.scalar_one() or 0)

        ride_query = select(Ride).where(
            Ride.driver_id == driver.id,
            Ride.status == RideStatus.COMPLETED.value,
        )
        if period_start is not None:
            ride_query = ride_query.where(Ride.completed_at >= period_start)
        ride_query = ride_query.order_by(Ride.completed_at.desc()).limit(50)

        rides_result = await self.db.execute(ride_query)
        rides = list(rides_result.scalars().all())

        completed_result = await self.db.execute(
            select(func.count())
            .select_from(Ride)
            .where(
                Ride.driver_id == driver.id,
                Ride.status == RideStatus.COMPLETED.value,
                *([Ride.completed_at >= period_start] if period_start is not None else []),
            )
        )
        period_rides = int(completed_result.scalar_one() or 0)

        return {
            "period": period,
            "total_rides": period_rides,
            "total_earnings": period_earnings,
            "net_earnings": period_earnings,
            "rides": [
                {
                    "ride_id": r.id,
                    "ride_fare": float(r.final_fare or r.estimated_fare or 0),
                    "driver_commission_percentage": float(r.driver_commission_percentage or 0),
                    "driver_earning": float(r.driver_earning or 0),
                    "ride_date": r.completed_at,
                    "status": r.status,
                }
                for r in rides
            ],
        }
