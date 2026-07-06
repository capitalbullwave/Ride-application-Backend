import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories.base import BaseRepository
from app.utils.phone import normalize_phone, phone_lookup_variants


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.email == email, User.is_deleted == False)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[User]:
        for variant in phone_lookup_variants(phone):
            result = await self.db.execute(
                select(User).where(User.phone == variant, User.is_deleted == False)
            )
            user = result.scalar_one_or_none()
            if user:
                return user

        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 10:
            local = digits[-10:]
            result = await self.db.execute(
                select(User).where(User.is_deleted == False, User.phone.like(f"%{local}"))
            )
            for user in result.scalars().all():
                stored = "".join(c for c in user.phone if c.isdigit())
                if stored.endswith(local):
                    return user
        return None

    async def find_for_otp(self, phone: str, email: str) -> Optional[User]:
        """Resolve an active user for OTP flows."""
        normalized = normalize_phone(phone)
        variants = phone_lookup_variants(normalized)
        digits = "".join(c for c in normalized if c.isdigit())
        local = digits[-10:] if len(digits) >= 10 else None

        conditions = [User.email == email, *[User.phone == variant for variant in variants]]
        email_local = email.split("@", 1)[0]
        if email_local:
            conditions.append(User.email.like(f"{email_local}@%"))
        if local:
            conditions.append(User.phone.like(f"%{local}"))

        result = await self.db.execute(
            select(User).where(or_(*conditions), User.is_deleted == False)
        )
        candidates = result.scalars().all()
        if not candidates:
            return None

        return next((item for item in candidates if item.email == email), candidates[0])

    async def get_by_id_active(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.id == user_id, User.is_deleted == False, User.is_active == True)
        )
        return result.scalar_one_or_none()
