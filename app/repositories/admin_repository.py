import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser, Wallet
from app.repositories.base import BaseRepository


class AdminRepository(BaseRepository[AdminUser]):
    def __init__(self, db: AsyncSession):
        super().__init__(AdminUser, db)

    async def get_by_email(self, email: str) -> Optional[AdminUser]:
        result = await self.db.execute(
            select(AdminUser).where(AdminUser.email == email, AdminUser.is_deleted == False)
        )
        return result.scalar_one_or_none()


class WalletRepository(BaseRepository[Wallet]):
    def __init__(self, db: AsyncSession):
        super().__init__(Wallet, db)

    async def get_by_user_id(self, user_id: uuid.UUID) -> Optional[Wallet]:
        result = await self.db.execute(select(Wallet).where(Wallet.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_driver_id(self, driver_id: uuid.UUID) -> Optional[Wallet]:
        result = await self.db.execute(select(Wallet).where(Wallet.driver_id == driver_id))
        return result.scalar_one_or_none()
