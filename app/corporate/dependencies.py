"""Corporate auth and service dependencies."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_token, _validate_token_version
from app.core.constants import UserRole
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.corporate.models import Company
from app.corporate.repository import CorporateRepository
from app.corporate.service import CorporateService
from app.database.session import get_db


async def get_corporate_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CorporateService:
    return CorporateService(db)


async def get_current_company(
    token: Annotated[dict, Depends(get_current_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Company:
    if token.get("role") != UserRole.COMPANY.value:
        raise ForbiddenException("Company access required")

    version = token.get("token_version")
    company_id = uuid.UUID(token["sub"])
    repo = CorporateRepository(db)
    company = await repo.get_company(company_id)
    if not company:
        raise UnauthorizedException("Company not found")
    if version is not None and company.token_version != version:
        raise UnauthorizedException("Session expired. Please login again.")
    return company


async def require_company_scope(
    company: Company,
    company_id: uuid.UUID,
) -> None:
    """Ensure a company admin can only access their own company."""
    if company.id != company_id:
        raise ForbiddenException("Access limited to your company")
