"""Unified ride APIs — /api/v1/rides/*"""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_driver, get_current_token, get_current_user, get_optional_current_user
from app.api.websocket.manager import manager
from app.core.constants import UserRole
from app.core.exceptions import ForbiddenException, NotFoundException
from app.core.logging import get_logger
from app.database.session import get_db
from app.models import Driver, User
from app.rides.dependencies import get_ride_service
from app.rides.schemas import (
    DriverAcceptRequest,
    RideBookRequest,
    RideCancelRequest,
    RideDetailResponse,
    RideEstimateRequest,
    RideEstimateResponse,
    RideOtpVerifyRequest,
    RideResponse,
)
from app.rides.service import RideService
from app.services.driver_matching import DriverMatchingService

logger = get_logger(__name__)

router = APIRouter(tags=["Rides"])


@router.post(
    "/estimate",
    response_model=RideEstimateResponse,
    summary="Estimate ride fare",
    description="Calculate distance, duration, and fare breakdown per vehicle type. Fare is computed server-side only.",
)
async def estimate_fare(
    payload: RideEstimateRequest,
    service: Annotated[RideService, Depends(get_ride_service)],
    user: Annotated[Optional[User], Depends(get_optional_current_user)] = None,
):
    return await service.fare.estimate(payload, user_id=user.id if user else None)


@router.post(
    "/book",
    response_model=RideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a ride",
    description="Create a ride request and begin driver matching (SEARCHING_DRIVER).",
)
async def book_ride(
    payload: RideBookRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RideService, Depends(get_ride_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    ride = await service.book(user.id, payload)
    logger.info(
        "ride_book_requested",
        ride_id=str(ride.id),
        user_id=str(user.id),
        vehicle_type_id=str(payload.vehicle_type_id),
        source="rides_router",
    )
    notified = await DriverMatchingService(db).dispatch_ride_to_online_drivers(ride, manager)
    logger.info(
        "ride_driver_search_dispatched",
        ride_id=str(ride.id),
        drivers_notified=notified,
        source="rides_router",
    )
    return RideService.to_response(ride)


@router.get(
    "/current",
    response_model=RideDetailResponse,
    summary="Get current active ride",
    description="Returns the authenticated user or driver's active ride.",
)
async def current_ride(
    token: Annotated[dict, Depends(get_current_token)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    subject = UUID(token["sub"])
    role = token.get("role")
    if role == UserRole.USER.value:
        ride = await service.crud.get_active_for_user(subject)
    elif role == UserRole.DRIVER.value:
        ride = await service.crud.get_active_for_driver(subject)
    else:
        raise ForbiddenException("Only users and drivers can access active rides")
    if not ride:
        raise NotFoundException("No active ride")
    ride = await service.get_ride(ride.id)
    return RideService.to_detail(ride)


@router.get(
    "/history",
    response_model=list[RideResponse],
    summary="Ride history (user)",
)
async def ride_history(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RideService, Depends(get_ride_service)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    rides = await service.crud.list_for_user(
        user.id, page=page, page_size=page_size, status=status_filter
    )
    return [RideService.to_response(r) for r in rides]


@router.get(
    "/driver/history",
    response_model=list[RideResponse],
    summary="Ride history (driver)",
)
async def driver_ride_history(
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    rides = await service.crud.list_for_driver(
        driver.id, page=page, page_size=page_size, status=status_filter
    )
    return [RideService.to_response(r) for r in rides]


@router.get(
    "/{ride_id}",
    response_model=RideDetailResponse,
    summary="Ride details with timeline",
)
async def ride_details(
    ride_id: UUID,
    token: Annotated[dict, Depends(get_current_token)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.get_ride(ride_id)
    subject = UUID(token["sub"])
    role = token.get("role")
    if role == UserRole.USER.value and ride.user_id != subject:
        raise ForbiddenException("Access denied")
    if role == UserRole.DRIVER.value and ride.driver_id != subject:
        raise ForbiddenException("Access denied")
    return RideService.to_detail(ride)


@router.post(
    "/{ride_id}/cancel",
    response_model=RideResponse,
    summary="Cancel ride (user)",
)
async def cancel_ride_user(
    ride_id: UUID,
    payload: RideCancelRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.cancel(
        ride_id,
        cancelled_by="USER",
        actor_id=user.id,
        reason=payload.reason,
    )
    return RideService.to_response(ride)


@router.post(
    "/{ride_id}/accept",
    response_model=RideResponse,
    summary="Accept ride (driver)",
)
async def accept_ride(
    ride_id: UUID,
    payload: DriverAcceptRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.accept(ride_id, driver.id, payload.vehicle_id)
    return RideService.to_response(ride)


@router.post(
    "/{ride_id}/reject",
    summary="Reject ride (driver)",
)
async def reject_ride(
    ride_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
    reason: str = Query("", max_length=300),
):
    return await service.reject(ride_id, driver.id, reason)


@router.post(
    "/{ride_id}/arrived",
    response_model=RideResponse,
    summary="Driver arrived at pickup",
)
async def driver_arrived(
    ride_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.driver_arrived(ride_id, driver.id)
    return RideService.to_response(ride)


@router.post(
    "/{ride_id}/verify-otp",
    response_model=RideResponse,
    summary="Verify ride OTP",
)
async def verify_ride_otp(
    ride_id: UUID,
    payload: RideOtpVerifyRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.verify_otp(ride_id, driver.id, payload.otp)
    return RideService.to_response(ride)


@router.post(
    "/{ride_id}/start",
    response_model=RideResponse,
    summary="Start ride",
)
async def start_ride(
    ride_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.start(ride_id, driver.id)
    return RideService.to_response(ride)


@router.post(
    "/{ride_id}/complete",
    response_model=RideResponse,
    summary="Complete ride",
)
async def complete_ride(
    ride_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    service: Annotated[RideService, Depends(get_ride_service)],
):
    ride = await service.complete(ride_id, driver.id)
    return RideService.to_response(ride)
