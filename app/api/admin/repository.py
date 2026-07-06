"""Admin module service layer."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import AuthService
from app.repositories.admin_repository import AdminRepository


class AdminApiService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.auth = AuthService(db)
        self.repo = AdminRepository(db)


__all__ = ["AdminApiService"]
