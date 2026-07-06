"""Aggregated driver dashboard statistics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RideStatus
from app.drivers.models import Driver
from app.models import Ride, RideEvent, WalletTransaction
from app.services.payment_service import WalletService


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
        wallet = await WalletService(self.db).get_or_create_wallet(driver_id=driver.id)
        today_start = _start_of_period_utc("daily")

        today_earnings_result = await self.db.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.transaction_type == "CREDIT",
                WalletTransaction.created_at >= today_start,
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
            "wallet_balance": float(wallet.balance or 0),
            "completed_trips": completed_trips,
            "today_trips": today_trips,
            "rating": round(float(driver.rating_avg or 0), 1),
            "acceptance_rate": acceptance_rate,
        }

    async def earnings_for_period(self, driver: Driver, period: str) -> dict:
        wallet = await WalletService(self.db).get_or_create_wallet(driver_id=driver.id)
        period_start = _start_of_period_utc(period)

        query = select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
            WalletTransaction.wallet_id == wallet.id,
            WalletTransaction.transaction_type == "CREDIT",
        )
        if period_start is not None:
            query = query.where(WalletTransaction.created_at >= period_start)

        credit_sum = await self.db.execute(query)
        period_earnings = float(credit_sum.scalar_one() or 0)

        completed_result = await self.db.execute(
            select(func.count())
            .select_from(Ride)
            .where(
                Ride.driver_id == driver.id,
                Ride.status == RideStatus.COMPLETED.value,
                *(
                    [Ride.completed_at >= period_start]
                    if period_start is not None
                    else []
                ),
            )
        )
        period_rides = int(completed_result.scalar_one() or 0)

        return {
            "period": period,
            "total_rides": period_rides,
            "total_earnings": period_earnings,
            "net_earnings": period_earnings,
        }
