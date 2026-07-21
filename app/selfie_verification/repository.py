"""Data access for driver shifts and selfie logs."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SelfieVerificationStatus, ShiftStatus
from app.selfie_verification.models import DriverSelfieLog, DriverShift


class DriverShiftRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_shift(self, driver_id: uuid.UUID) -> DriverShift | None:
        result = await self.db.execute(
            select(DriverShift)
            .where(
                DriverShift.driver_id == driver_id,
                DriverShift.status == ShiftStatus.ACTIVE.value,
            )
            .order_by(DriverShift.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_stale_active_shifts(
        self,
        *,
        max_age: timedelta,
        before_date,
    ) -> list[DriverShift]:
        now = datetime.now(timezone.utc)
        cutoff = now - max_age
        result = await self.db.execute(
            select(DriverShift).where(
                DriverShift.status == ShiftStatus.ACTIVE.value,
                or_(
                    DriverShift.started_at < cutoff,
                    func.date(DriverShift.started_at) < before_date,
                ),
            )
        )
        return list(result.scalars().all())

    async def create_shift(self, shift: DriverShift) -> DriverShift:
        self.db.add(shift)
        await self.db.flush()
        return shift

    async def save(self, shift: DriverShift) -> DriverShift:
        await self.db.flush()
        return shift

    async def list_for_driver(
        self, driver_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[DriverShift]:
        result = await self.db.execute(
            select(DriverShift)
            .where(DriverShift.driver_id == driver_id)
            .order_by(DriverShift.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())


class DriverSelfieLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, log: DriverSelfieLog) -> DriverSelfieLog:
        self.db.add(log)
        await self.db.flush()
        return log

    async def save(self, log: DriverSelfieLog) -> DriverSelfieLog:
        await self.db.flush()
        return log

    async def get(self, log_id: uuid.UUID) -> DriverSelfieLog | None:
        result = await self.db.execute(
            select(DriverSelfieLog).where(DriverSelfieLog.id == log_id)
        )
        return result.scalar_one_or_none()

    async def get_consumable_success(
        self,
        driver_id: uuid.UUID,
        *,
        since: datetime,
    ) -> DriverSelfieLog | None:
        result = await self.db.execute(
            select(DriverSelfieLog)
            .where(
                DriverSelfieLog.driver_id == driver_id,
                DriverSelfieLog.status == SelfieVerificationStatus.SUCCESS.value,
                DriverSelfieLog.matched.is_(True),
                DriverSelfieLog.liveness_passed.is_(True),
                DriverSelfieLog.consumed_for_shift.is_(False),
                DriverSelfieLog.created_at >= since,
            )
            .order_by(DriverSelfieLog.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_recent_failures(
        self, driver_id: uuid.UUID, *, since: datetime
    ) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(DriverSelfieLog)
            .where(
                DriverSelfieLog.driver_id == driver_id,
                DriverSelfieLog.status == SelfieVerificationStatus.FAILED.value,
                DriverSelfieLog.created_at >= since,
            )
        )
        return int(result.scalar() or 0)

    async def list_for_driver(
        self,
        driver_id: uuid.UUID | None = None,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DriverSelfieLog]:
        query = select(DriverSelfieLog).order_by(DriverSelfieLog.created_at.desc())
        if driver_id:
            query = query.where(DriverSelfieLog.driver_id == driver_id)
        if status:
            query = query.where(DriverSelfieLog.status == status)
        result = await self.db.execute(query.offset(offset).limit(limit))
        return list(result.scalars().all())

    async def mark_consumed(self, log_id: uuid.UUID, shift_id: uuid.UUID) -> None:
        await self.db.execute(
            update(DriverSelfieLog)
            .where(DriverSelfieLog.id == log_id)
            .values(consumed_for_shift=True, shift_id=shift_id)
        )
