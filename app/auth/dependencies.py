import uuid
from typing import Annotated, Optional

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_token
from app.database.session import get_db
from app.models import AdminUser, Driver, User
from app.repositories.admin_repository import AdminRepository
from app.repositories.driver_repository import DriverRepository
from app.repositories.user_repository import UserRepository

security = HTTPBearer(auto_error=False)


async def get_current_token(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
) -> dict:
    if not credentials:
        raise UnauthorizedException("Authentication required")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as e:
        raise UnauthorizedException("Invalid or expired token") from e
    if payload.get("type") != "access":
        raise UnauthorizedException("Invalid token type")
    return payload


async def _validate_token_version(token: dict, db: AsyncSession) -> None:
    version = token.get("token_version")
    if version is None:
        return
    subject = uuid.UUID(token["sub"])
    role = token.get("role")
    if role == UserRole.USER.value:
        repo = UserRepository(db)
        user = await repo.get_by_id_active(subject)
        if not user or user.token_version != version:
            raise UnauthorizedException("Session expired. Please login again.")
    elif role == UserRole.DRIVER.value:
        repo = DriverRepository(db)
        driver = await repo.get_by_id_active(subject)
        if not driver or driver.token_version != version:
            raise UnauthorizedException("Session expired. Please login again.")


async def get_optional_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Optional[User]:
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        return None
    if payload.get("type") != "access" or payload.get("role") != UserRole.USER.value:
        return None
    try:
        await _validate_token_version(payload, db)
    except UnauthorizedException:
        return None
    repo = UserRepository(db)
    return await repo.get_by_id_active(uuid.UUID(payload["sub"]))


async def get_current_user(
    token: Annotated[dict, Depends(get_current_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if token.get("role") != UserRole.USER.value:
        raise ForbiddenException("User access required")
    await _validate_token_version(token, db)
    repo = UserRepository(db)
    user = await repo.get_by_id_active(uuid.UUID(token["sub"]))
    if not user:
        raise UnauthorizedException("User not found or inactive")
    return user


async def get_current_driver(
    token: Annotated[dict, Depends(get_current_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Driver:
    if token.get("role") != UserRole.DRIVER.value:
        raise ForbiddenException("Driver access required")
    await _validate_token_version(token, db)
    repo = DriverRepository(db)
    driver = await repo.get_by_id_active(uuid.UUID(token["sub"]))
    if not driver:
        raise UnauthorizedException("Driver not found or inactive")
    return driver


async def get_current_admin(
    token: Annotated[dict, Depends(get_current_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    role = token.get("role")
    if role not in (UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value):
        raise ForbiddenException("Admin access required")
    repo = AdminRepository(db)
    admin = await repo.get_by_id(uuid.UUID(token["sub"]))
    if not admin or not admin.is_active or admin.is_deleted:
        raise UnauthorizedException("Admin not found or inactive")
    return admin


def require_roles(*roles: UserRole):
    async def role_checker(token: Annotated[dict, Depends(get_current_token)]) -> dict:
        if token.get("role") not in [r.value for r in roles]:
            raise ForbiddenException(f"Required roles: {[r.value for r in roles]}")
        return token

    return role_checker


async def get_client_ip(x_forwarded_for: Annotated[Optional[str], Header()] = None) -> Optional[str]:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return None
