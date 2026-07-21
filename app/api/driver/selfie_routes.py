"""Driver selfie verification & shift endpoints."""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.driver.dependencies import get_current_driver
from app.database.session import get_db
from app.models import Driver
from app.selfie_verification.schemas import (
    GoOfflineResponse,
    GoOnlineResponse,
    LivenessChallengeResponse,
    SelfieVerifyRequest,
    SelfieVerifyResponse,
    ShiftResponse,
    VerificationStatusResponse,
)
from app.selfie_verification.service import DriverSelfieShiftService

router = APIRouter(tags=["Driver Selfie Verification"])


@router.get("/selfie/liveness-challenge", response_model=LivenessChallengeResponse)
async def issue_liveness_challenge(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverSelfieShiftService(db).issue_liveness_challenge(driver)


@router.post("/selfie/verify", response_model=SelfieVerifyResponse)
async def verify_selfie(
    data: SelfieVerifyRequest,
    request: Request,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else None
    return await DriverSelfieShiftService(db).verify_selfie(driver, data, ip_address=ip)


@router.get("/verification-status", response_model=VerificationStatusResponse)
async def verification_status(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverSelfieShiftService(db).get_verification_status(driver)


@router.get("/current-shift", response_model=Optional[ShiftResponse])
async def current_shift(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverSelfieShiftService(db).get_current_shift(driver)


@router.post("/go-online", response_model=GoOnlineResponse)
async def go_online_post(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    result = await DriverSelfieShiftService(db).go_online(driver)
    # Mirror PUT /go-online: attach fresh searching rides after coming online.
    try:
        from app.api.websocket.manager import manager
        from app.core.logging import get_logger
        from app.services.driver_matching import DriverMatchingService

        logger = get_logger(__name__)
        matching = DriverMatchingService(db)
        open_rides = await matching.list_open_searching_rides_for_driver(driver, limit=3)
        for ride in open_rides:
            await matching.remember_pending_ride(driver.id, ride.id)
            await manager.send_personal(
                str(driver.id),
                {
                    "event": "ride_request",
                    "ride_id": str(ride.id),
                    "pickup_address": ride.pickup_address,
                    "dropoff_address": ride.dropoff_address,
                    "pickup_lat": ride.pickup_lat,
                    "pickup_lng": ride.pickup_lng,
                    "dropoff_lat": ride.dropoff_lat,
                    "dropoff_lng": ride.dropoff_lng,
                    "stops": list(ride.stops or []),
                    "estimated_fare": float(ride.estimated_fare or 0),
                    "estimated_distance_km": float(ride.estimated_distance_km or 0),
                    "estimated_duration_min": float(ride.estimated_duration_min or 0),
                    "payment_method": ride.payment_method,
                    "passenger_name": (
                        f"{ride.user.first_name} {ride.user.last_name}".strip()
                        if ride.user
                        else "Passenger"
                    ),
                    "passenger_phone": ride.user.phone if ride.user else None,
                    "status": ride.status,
                    "actions": ["accept", "reject"],
                },
            )
        if open_rides:
            logger.info(
                "driver_online_open_rides_attached",
                driver_id=str(driver.id),
                count=len(open_rides),
            )
    except Exception:
        pass
    return result


@router.post("/go-offline", response_model=GoOfflineResponse)
async def go_offline_post(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverSelfieShiftService(db).go_offline(driver)
