"""Ride module dependencies."""
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_driver, get_current_token, get_current_user
from app.core.constants import UserRole
from app.core.exceptions import ForbiddenException
from app.database.session import get_db
from app.models import Driver, Ride, User
from app.rides.crud import RideCRUD
from app.rides.service import RideService


def get_ride_service(db: Annotated[AsyncSession, Depends(get_db)]) -> RideService:
    return RideService(db)


async def get_ride_for_user(
    ride_id: Annotated[UUID, Path(description="Ride UUID")],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Ride:
    ride = await RideCRUD(db).get_by_id(ride_id)
    if not ride or ride.user_id != user.id:
        raise ForbiddenException("Ride not found or access denied")
    return ride


async def get_ride_for_driver(
    ride_id: Annotated[UUID, Path(description="Ride UUID")],
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Ride:
    ride = await RideCRUD(db).get_by_id(ride_id)
    if not ride or ride.driver_id != driver.id:
        raise ForbiddenException("Ride not found or access denied")
    return ride


async def get_ride_for_participant(
    ride_id: Annotated[UUID, Path(description="Ride UUID")],
    token: Annotated[dict, Depends(get_current_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Ride:
    ride = await RideCRUD(db).get_by_id(ride_id)
    if not ride:
        raise ForbiddenException("Ride not found")
    role = token.get("role")
    subject = UUID(token["sub"])
    if role == UserRole.USER.value and ride.user_id == subject:
        return ride
    if role == UserRole.DRIVER.value and ride.driver_id == subject:
        return ride
    raise ForbiddenException("Access denied")
