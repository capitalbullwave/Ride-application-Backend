from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.driver_repository import DriverRepository


class DriverApiService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DriverRepository(db)


__all__ = ["DriverApiService"]
