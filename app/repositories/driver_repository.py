import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Driver
from app.repositories.base import BaseRepository
from app.utils.phone import normalize_phone, phone_lookup_variants


class DriverRepository(BaseRepository[Driver]):
    def __init__(self, db: AsyncSession):
        super().__init__(Driver, db)

    async def get_by_email(self, email: str) -> Optional[Driver]:
        result = await self.db.execute(
            select(Driver).where(Driver.email == email, Driver.is_deleted == False)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[Driver]:
        for variant in phone_lookup_variants(phone):
            result = await self.db.execute(
                select(Driver).where(Driver.phone == variant, Driver.is_deleted == False)
            )
            driver = result.scalar_one_or_none()
            if driver:
                return driver

        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 10:
            local = digits[-10:]
            result = await self.db.execute(
                select(Driver).where(Driver.is_deleted == False, Driver.phone.like(f"%{local}"))
            )
            for driver in result.scalars().all():
                stored = "".join(c for c in driver.phone if c.isdigit())
                if stored.endswith(local):
                    return driver
        return None

    async def find_for_otp(self, phone: str, email: str) -> Optional[Driver]:
        """Resolve an active driver for OTP flows."""
        normalized = normalize_phone(phone)
        variants = phone_lookup_variants(normalized)
        digits = "".join(c for c in normalized if c.isdigit())
        local = digits[-10:] if len(digits) >= 10 else None

        conditions = [Driver.email == email, *[Driver.phone == variant for variant in variants]]
        email_local = email.split("@", 1)[0]
        if email_local:
            conditions.append(Driver.email.like(f"{email_local}@%"))
        if local:
            conditions.append(Driver.phone.like(f"%{local}"))

        result = await self.db.execute(
            select(Driver).where(or_(*conditions), Driver.is_deleted == False)
        )
        candidates = result.scalars().all()
        if not candidates:
            return None

        return next((item for item in candidates if item.email == email), candidates[0])

    async def get_by_id_active(self, driver_id: uuid.UUID) -> Optional[Driver]:
        result = await self.db.execute(
            select(Driver).where(
                Driver.id == driver_id, Driver.is_deleted == False, Driver.is_active == True
            )
        )
        return result.scalar_one_or_none()
