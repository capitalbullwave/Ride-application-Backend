"""Driver Panel API — /api/v1/driver/*"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.driver.dependencies import get_current_driver
from app.core.constants import DriverStatus, KYCStatus, PaymentStatus, RideStatus, SupportTicketPriority, SupportTicketStatus
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.database.session import get_db
from app.models import Driver, DriverDocument, Notification, Ride, SupportTicket, Vehicle, WalletTransaction
from app.repositories.driver_repository import DriverRepository
from app.repositories.ride_repository import RideRepository
from app.schemas.driver import (
    DriverBankResponse,
    DriverBankUpsert,
    DriverDocumentCreate,
    DriverDashboardResponse,
    DriverEarningsResponse,
    DriverLocationUpdate,
    DriverRegistrationComplete,
    DriverRegistrationProgressResponse,
    DriverResponse,
    DriverSavedRegistrationData,
    DriverUpdate,
    DriverVehicleCreate,
    EmergencyContactCreate,
    EmergencyContactResponse,
    EmergencyContactUpdate,
    SaveKycStep,
    SaveLicenseNumber,
    SaveLicenseUpload,
    SaveProfileStep,
    SaveVehicleNumberStep,
)
from app.services.driver_registration_service import DriverRegistrationService
from app.services.driver_registration_progress_service import (
    DriverRegistrationProgressService,
)
from app.schemas.ride import RideOTPVerify, RideResponse
from app.notifications.service import NotificationService, serialize_driver_notification
from app.services.driver_emergency_contact_service import (
    DriverEmergencyContactService,
    contact_to_response,
)
from app.services.driver_matching import DriverMatchingService
from app.services.payment_service import PaymentService, WalletService
from app.services.ride_service import RideService
from app.api.websocket.manager import manager

router = APIRouter(tags=["Driver"])


def _driver_active_ride_payload(ride: Ride) -> dict:
    """Enriched ride payload for the driver app (passenger + fare from DB)."""
    payload = RideResponse.model_validate(ride).model_dump(mode="json")
    payload["dropoff_address"] = ride.dropoff_address
    payload["payment_method"] = ride.payment_method
    payload["estimated_distance_km"] = ride.estimated_distance_km
    if ride.user:
        payload["passenger_name"] = f"{ride.user.first_name} {ride.user.last_name}".strip() or "Passenger"
        payload["passenger_phone"] = ride.user.phone
    return payload


async def _load_driver_ride(db: AsyncSession, ride_id: UUID) -> Ride:
    from sqlalchemy.orm import selectinload

    loaded = await db.execute(
        select(Ride)
        .options(selectinload(Ride.user), selectinload(Ride.vehicle))
        .where(Ride.id == ride_id)
    )
    return loaded.scalar_one()


def _payment_breakdown_payload(ride: Ride, payment_method: str | None = None) -> dict:
    fare = float(ride.final_fare or ride.estimated_fare or 0)
    commission = round(fare * 0.2, 2)
    return {
        "trip_fare": fare,
        "commission": commission,
        "bonus": 0.0,
        "total_earnings": round(fare - commission, 2),
        "payment_mode": payment_method or ride.payment_method or "CASH",
        "final_fare": fare,
        "estimated_fare": ride.estimated_fare,
        "payment_method": payment_method or ride.payment_method or "CASH",
    }


async def _credit_driver_for_ride(db: AsyncSession, driver: Driver, ride: Ride) -> float:
    fare = float(ride.final_fare or ride.estimated_fare or 0)
    if fare <= 0:
        return 0.0

    driver_share = round(fare * 0.85, 2)
    wallet = await WalletService(db).get_or_create_wallet(driver_id=driver.id)
    await WalletService(db).credit(
        wallet.id,
        driver_share,
        f"Earnings for ride {str(ride.id)[:8]}",
        str(ride.id),
    )
    notif = NotificationService(db)
    await notif.create_in_app(
        title="Ride earnings credited",
        message=f"₹{driver_share:.2f} added to your wallet.",
        notification_type="PAYMENT",
        driver_id=driver.id,
        data={"ride_id": str(ride.id), "amount": driver_share},
    )
    await notif.create_in_app(
        title="Rate your driver",
        message="How was your trip? Tap to rate your captain.",
        notification_type="RIDE",
        user_id=ride.user_id,
        data={"ride_id": str(ride.id), "event": "rate_driver"},
    )
    return driver_share


class AcceptRideRequest(BaseModel):
    ride_id: UUID
    vehicle_id: UUID | None = None


class RejectRideRequest(BaseModel):
    ride_id: UUID
    reason: str | None = None


class StartRideRequest(BaseModel):
    ride_id: UUID
    otp: str


class EndRideRequest(BaseModel):
    ride_id: UUID


class CollectPaymentRequest(BaseModel):
    ride_id: UUID
    method: str = Field(..., pattern="^(CASH|RAZORPAY)$")


@router.get("/profile", response_model=DriverResponse)
async def get_profile(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.driver_dashboard_service import DriverDashboardService

    completed_trips = await DriverDashboardService(db).count_completed_trips(driver.id)
    return DriverResponse.model_validate(driver).model_copy(
        update={"total_rides": completed_trips}
    )


@router.put("/profile", response_model=DriverResponse)
async def update_profile(
    data: DriverUpdate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    repo = DriverRepository(db)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(driver, field, value)
    await repo.update(driver)
    return DriverResponse.model_validate(driver)


@router.post("/upload-license")
async def upload_license(
    data: DriverDocumentCreate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    service = DriverRegistrationProgressService(db)
    side = "back" if data.document_type.upper().endswith("BACK") else "front"
    return await service.save_license_upload(
        driver,
        SaveLicenseUpload(document_url=data.document_url, side=side),
    )


@router.get("/registration-progress", response_model=DriverRegistrationProgressResponse)
async def get_registration_progress(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).get_progress(driver)


@router.get("/registration-data", response_model=DriverSavedRegistrationData)
async def get_registration_data(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).get_saved_data(driver)


@router.post("/registration/license-upload")
async def registration_license_upload(
    data: SaveLicenseUpload,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).save_license_upload(driver, data)


@router.patch("/registration/license-number")
async def registration_license_number(
    data: SaveLicenseNumber,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).save_license_number(driver, data)


@router.patch("/registration/profile")
async def registration_profile(
    data: SaveProfileStep,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).save_profile(driver, data)


@router.post("/registration/vehicle")
async def registration_vehicle(
    data: SaveVehicleNumberStep,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).save_vehicle_number(driver, data)


@router.post("/registration/kyc")
async def registration_kyc(
    data: SaveKycStep,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).save_kyc(driver, data)


@router.post("/registration/submit")
async def registration_submit(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationProgressService(db).submit(driver)


@router.post("/upload-vehicle")
async def upload_vehicle(
    data: DriverVehicleCreate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    vehicle = Vehicle(
        driver_id=driver.id,
        vehicle_type_id=data.vehicle_type_id,
        license_plate=data.license_plate,
        make=data.make or data.model,
        model=data.model,
        color=data.color,
        year=data.year,
    )
    db.add(vehicle)
    await db.flush()
    return {"id": str(vehicle.id), "license_plate": vehicle.license_plate}


@router.post("/complete-registration")
async def complete_registration(
    data: DriverRegistrationComplete,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    return await DriverRegistrationService(db).complete_registration(driver, data)


@router.put("/go-online")
async def go_online(driver: Annotated[Driver, Depends(get_current_driver)], db: AsyncSession = Depends(get_db)):
    if driver.kyc_status != KYCStatus.APPROVED.value:
        if driver.kyc_status == KYCStatus.REJECTED.value:
            raise ForbiddenException(
                "Your documents were rejected. Please update and resubmit before going online."
            )
        raise ForbiddenException(
            "Account verification is pending. You can go online after admin approval."
        )
    if not driver.is_verified:
        raise ForbiddenException(
            "Phone verification is required before going online."
        )

    driver.status = DriverStatus.ONLINE.value
    await DriverRepository(db).update(driver)
    matching = DriverMatchingService(db)
    lat, lng = await matching.driver_default_location(driver.id)
    await matching.ensure_driver_online(driver, lat, lng)
    return {"status": driver.status}


@router.put("/go-offline")
async def go_offline(driver: Annotated[Driver, Depends(get_current_driver)], db: AsyncSession = Depends(get_db)):
    driver.status = DriverStatus.OFFLINE.value
    await DriverRepository(db).update(driver)
    await DriverMatchingService(db).set_driver_offline(driver.id)
    return {"status": driver.status}


@router.post("/location")
async def update_location(
    data: DriverLocationUpdate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    await DriverMatchingService(db).update_driver_location(driver.id, data.lat, data.lng, data.heading, data.speed)
    if driver.status == DriverStatus.ONLINE.value:
        await DriverMatchingService(db).ensure_driver_online(driver, data.lat, data.lng)

    active = await RideRepository(db).get_active_ride_for_driver(driver.id)
    if active:
        location_payload = {
            "event": "driver_location",
            "ride_id": str(active.id),
            "driver_id": str(driver.id),
            "lat": data.lat,
            "lng": data.lng,
            "heading": data.heading,
        }
        await manager.send_personal(str(active.user_id), location_payload)
        await manager.broadcast_ride(str(active.id), location_payload)

    return {"message": "Location updated"}


@router.get("/ride-requests")
async def ride_requests(driver: Annotated[Driver, Depends(get_current_driver)], db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload

    matching = DriverMatchingService(db)
    pending_ids = await matching.get_pending_ride_ids(driver.id)

    if not pending_ids:
        return []

    query = (
        select(Ride)
        .options(selectinload(Ride.user))
        .where(
            Ride.id.in_(pending_ids),
            Ride.status == RideStatus.SEARCHING_DRIVER.value,
        )
        .order_by(Ride.created_at.desc())
        .limit(20)
    )

    result = await db.execute(query)
    rides = list(result.scalars().all())

    for ride in rides:
        await matching.remember_pending_ride(driver.id, ride.id)

    return [
        {
            "id": str(r.id),
            "pickup_address": r.pickup_address,
            "dropoff_address": r.dropoff_address,
            "pickup_lat": r.pickup_lat,
            "pickup_lng": r.pickup_lng,
            "dropoff_lat": r.dropoff_lat,
            "dropoff_lng": r.dropoff_lng,
            "estimated_fare": r.estimated_fare,
            "estimated_distance_km": r.estimated_distance_km,
            "estimated_duration_min": r.estimated_duration_min,
            "payment_method": r.payment_method,
            "passenger_name": (
                f"{r.user.first_name} {r.user.last_name}".strip() if r.user else "Passenger"
            ),
            "passenger_phone": r.user.phone if r.user else None,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


@router.post("/accept-ride", response_model=RideResponse)
async def accept_ride(
    data: AcceptRideRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload

    from app.models import Vehicle
    from app.notifications.service import NotificationService

    matching = DriverMatchingService(db)
    vehicle_id = data.vehicle_id
    vehicle = None
    if vehicle_id is None:
        vehicle_result = await db.execute(
            select(Vehicle).where(
                Vehicle.driver_id == driver.id,
                Vehicle.is_deleted.is_(False),
            ).limit(1)
        )
        vehicle = vehicle_result.scalar_one_or_none()
        if not vehicle:
            raise ValidationException("Register a vehicle before accepting rides")
        vehicle_id = vehicle.id
    else:
        vehicle_result = await db.execute(
            select(Vehicle).where(
                Vehicle.id == vehicle_id,
                Vehicle.driver_id == driver.id,
                Vehicle.is_deleted.is_(False),
            )
        )
        vehicle = vehicle_result.scalar_one_or_none()
        if not vehicle:
            raise ValidationException("Vehicle not found for this driver")

    ride = await RideService(db).accept_ride(data.ride_id, driver.id, vehicle_id)

    loaded = await _load_driver_ride(db, data.ride_id)
    ride = loaded

    driver_name = f"{driver.first_name} {driver.last_name}".strip()
    accept_payload = {
        "event": "ride_accepted",
        "ride_id": str(data.ride_id),
        "driver_id": str(driver.id),
        "driver_name": driver_name,
        "driver_phone": driver.phone,
        "vehicle_number": vehicle.license_plate if vehicle else None,
        "start_code": ride.ride_otp,
        "status": ride.status,
        "pickup_address": ride.pickup_address,
        "dropoff_address": ride.dropoff_address,
        "pickup_lat": ride.pickup_lat,
        "pickup_lng": ride.pickup_lng,
        "dropoff_lat": ride.dropoff_lat,
        "dropoff_lng": ride.dropoff_lng,
        "estimated_fare": ride.estimated_fare,
    }
    # Push to user immediately over websocket before slower side effects.
    await manager.send_personal(str(ride.user_id), accept_payload)
    await manager.broadcast_ride(str(data.ride_id), accept_payload)

    response = _driver_active_ride_payload(ride)

    import asyncio

    ride_id = data.ride_id
    driver_id = driver.id

    async def _accept_side_effects() -> None:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as bg_db:
            try:
                bg_ride = await _load_driver_ride(bg_db, ride_id)
                bg_driver = await bg_db.get(Driver, driver_id)
                bg_vehicle = await bg_db.get(Vehicle, vehicle_id)
                if bg_driver is not None:
                    await NotificationService(bg_db).notify_user_ride_accepted(
                        bg_ride, bg_driver, bg_vehicle
                    )
                await NotificationService(bg_db).close_all_ride_requests_for_ride(
                    ride_id, "taken"
                )
                await bg_db.commit()
            except Exception:
                await bg_db.rollback()

            bg_matching = DriverMatchingService(bg_db)
            try:
                await asyncio.wait_for(bg_matching.clear_ride_requests(ride_id), timeout=0.5)
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    bg_matching.clear_driver_pending(driver_id, ride_id),
                    timeout=0.5,
                )
            except Exception:
                pass

    asyncio.create_task(_accept_side_effects())

    return response


@router.post("/reject-ride")
async def reject_ride(
    data: RejectRideRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    await DriverMatchingService(db).clear_driver_pending(driver.id, data.ride_id)
    await NotificationService(db).close_driver_ride_request(
        driver.id, data.ride_id, "rejected"
    )
    return {"ride_id": str(data.ride_id), "status": "rejected", "reason": data.reason}


@router.post("/start-ride", response_model=RideResponse)
async def start_ride(
    data: StartRideRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ride = await RideService(db).get_ride(data.ride_id)
    if ride.driver_id != driver.id:
        raise ForbiddenException("Access denied")
    ride = await RideService(db).verify_otp_and_start(data.ride_id, data.otp)
    await manager.broadcast_ride(str(data.ride_id), {"event": "ride_started", "ride_id": str(data.ride_id)})
    loaded = await _load_driver_ride(db, data.ride_id)
    await manager.send_personal(
        str(loaded.user_id),
        {
            "event": "ride_started",
            "ride_id": str(data.ride_id),
            "status": loaded.status,
        },
    )
    return _driver_active_ride_payload(loaded)


@router.post("/end-ride", response_model=RideResponse)
async def end_ride(
    data: EndRideRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ride = await RideService(db).get_ride(data.ride_id)
    if ride.driver_id != driver.id:
        raise ForbiddenException("Access denied")
    ride = await RideService(db).complete_ride(data.ride_id)
    driver.total_rides = (driver.total_rides or 0) + 1
    await DriverRepository(db).update(driver)
    await manager.broadcast_ride(str(data.ride_id), {"event": "ride_completed", "ride_id": str(data.ride_id)})
    await manager.send_personal(
        str(ride.user_id),
        {
            "event": "ride_completed",
            "ride_id": str(data.ride_id),
            "fare": ride.final_fare or ride.estimated_fare,
        },
    )
    payload = RideResponse.model_validate(ride).model_dump(mode="json")
    payload.update(_payment_breakdown_payload(ride))
    existing_payment = await PaymentService(db).get_ride_payment(ride.id)
    payload["payment_collected"] = (
        existing_payment is not None and existing_payment.status == PaymentStatus.COMPLETED.value
    )
    payload["payment_status"] = existing_payment.status if existing_payment else PaymentStatus.PENDING.value
    return payload


@router.post("/collect-payment")
async def collect_payment(
    data: CollectPaymentRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.core.constants import PaymentMethod

    ride = await RideService(db).get_ride(data.ride_id)
    if ride.driver_id != driver.id:
        raise ForbiddenException("Access denied")
    if ride.status != RideStatus.COMPLETED.value:
        raise ValidationException("Ride must be completed before collecting payment")

    fare = float(ride.final_fare or ride.estimated_fare or 0)
    if fare <= 0:
        raise ValidationException("Ride fare is not available")

    payment_service = PaymentService(db)
    existing = await payment_service.get_ride_payment(ride.id)
    if existing and existing.status == PaymentStatus.COMPLETED.value:
        payload = _payment_breakdown_payload(ride, existing.payment_method)
        payload.update(
            {
                "success": True,
                "payment_status": PaymentStatus.COMPLETED.value,
                "payment_collected": True,
            }
        )
        return payload

    method = data.method.strip().upper()
    if method == "CASH":
        payment = await payment_service.process_payment(ride.id, ride.user_id, fare, PaymentMethod.CASH.value)
        ride.payment_method = PaymentMethod.CASH.value
        await db.flush()
        await _credit_driver_for_ride(db, driver, ride)
        await db.commit()
        payload = _payment_breakdown_payload(ride, PaymentMethod.CASH.value)
        payload.update(
            {
                "success": True,
                "payment_status": payment.status,
                "payment_collected": payment.status == PaymentStatus.COMPLETED.value,
            }
        )
        return payload

    payment = await payment_service.create_ride_qr_payment(ride.id, ride.user_id, fare)
    ride.payment_method = PaymentMethod.RAZORPAY.value
    await db.commit()
    qr_data = payment.gateway_response or {}
    payload = _payment_breakdown_payload(ride, PaymentMethod.RAZORPAY.value)
    payload.update(
        {
            "success": True,
            "payment_status": payment.status,
            "payment_collected": False,
            "qr_code_id": qr_data.get("qr_code_id") or qr_data.get("payment_link_id"),
            "payment_link_id": qr_data.get("payment_link_id") or qr_data.get("qr_code_id"),
            "short_url": qr_data.get("short_url"),
            "image_url": qr_data.get("image_url"),
            "amount": fare,
            "key_id": qr_data.get("key_id"),
        }
    )
    return payload


@router.get("/collect-payment/{ride_id}/status")
async def collect_payment_status(
    ride_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ride = await RideService(db).get_ride(ride_id)
    if ride.driver_id != driver.id:
        raise ForbiddenException("Access denied")

    payment_service = PaymentService(db)
    payment = await payment_service.get_ride_payment(ride_id)
    if not payment:
        raise ValidationException("Payment has not been started for this ride")

    if payment.status != PaymentStatus.COMPLETED.value:
        payment = await payment_service.refresh_ride_qr_payment(ride_id)
        if payment.status == PaymentStatus.COMPLETED.value:
            await _credit_driver_for_ride(db, driver, ride)
            await db.commit()

    payload = _payment_breakdown_payload(ride, payment.payment_method)
    payload.update(
        {
            "success": True,
            "payment_status": payment.status,
            "payment_collected": payment.status == PaymentStatus.COMPLETED.value,
        }
    )
    return payload


class RatePassengerRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


@router.post("/ride/{ride_id}/rate")
async def rate_passenger(
    ride_id: UUID,
    data: RatePassengerRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.rating_service import RatingService

    return await RatingService(db).rate_user(ride_id, driver, data.rating, data.comment)


@router.get("/dashboard", response_model=DriverDashboardResponse)
async def driver_dashboard(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.driver_dashboard_service import DriverDashboardService

    stats = await DriverDashboardService(db).get_stats(driver)
    return DriverDashboardResponse(**stats)


@router.get("/wallet")
async def driver_wallet(driver: Annotated[Driver, Depends(get_current_driver)], db: AsyncSession = Depends(get_db)):
    from app.services.driver_bank_service import DriverBankService, bank_to_response
    from app.services.payment_service import WalletService

    wallet = await WalletService(db).get_or_create_wallet(driver_id=driver.id)
    bank = await DriverBankService(db).get_primary(driver.id)
    payload: dict = {"balance": wallet.balance}
    if bank:
        payload["bank"] = bank_to_response(bank).model_dump()
    return payload


@router.get("/bank", response_model=DriverBankResponse)
async def get_bank(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.driver_bank_service import DriverBankService, bank_to_response

    bank = await DriverBankService(db).get_primary(driver.id)
    if not bank:
        raise NotFoundException("No bank account linked")
    return bank_to_response(bank)


@router.post("/bank", response_model=DriverBankResponse)
async def save_bank(
    data: DriverBankUpsert,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.driver_bank_service import DriverBankService

    bank = await DriverBankService(db).upsert(driver.id, data)
    return bank


@router.get("/earnings", response_model=DriverEarningsResponse)
async def earnings(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
    period: str = Query("daily"),
):
    from app.services.driver_dashboard_service import DriverDashboardService

    payload = await DriverDashboardService(db).earnings_for_period(driver, period)
    return DriverEarningsResponse(**payload)


@router.get("/wallet/transactions")
async def driver_wallet_transactions(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.services.payment_service import WalletService

    wallet = await WalletService(db).get_or_create_wallet(driver_id=driver.id)
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    txns = result.scalars().all()
    return {
        "data": [
            {
                "id": str(t.id),
                "type": t.transaction_type.lower(),
                "amount": t.amount,
                "description": t.description,
                "reference_id": t.reference_id,
                "created_at": t.created_at.isoformat(),
            }
            for t in txns
        ],
        "page": page,
        "page_size": page_size,
    }


_DOC_LABELS = {
    "DRIVING_LICENSE": "Driving License",
    "DRIVING_LICENSE_BACK": "Driving License (Back)",
    "AADHAAR": "Aadhaar Card",
    "AADHAAR_BACK": "Aadhaar Card (Back)",
    "PAN": "PAN Card",
    "VEHICLE_RC": "Vehicle RC",
    "VEHICLE_RC_BACK": "Vehicle RC (Back)",
    "INSURANCE": "Insurance",
}


@router.get("/documents")
async def driver_documents(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DriverDocument).where(DriverDocument.driver_id == driver.id).order_by(DriverDocument.created_at.desc())
    )
    return {
        "data": [
            {
                "id": str(doc.id),
                "type": _DOC_LABELS.get(doc.document_type, doc.document_type.replace("_", " ").title()),
                "status": doc.status.lower(),
                "document_url": doc.document_url,
                "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
                "is_expiring_soon": False,
            }
            for doc in result.scalars().all()
        ]
    }


class DriverSupportRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=5)


class DriverSosRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    message: str | None = None


@router.post("/support")
async def driver_create_support(
    data: DriverSupportRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ticket = SupportTicket(
        driver_id=driver.id,
        subject=data.subject.strip(),
        description=data.message.strip(),
        status=SupportTicketStatus.OPEN.value,
        priority=SupportTicketPriority.MEDIUM.value,
    )
    db.add(ticket)
    await db.flush()
    return {"id": str(ticket.id), "subject": ticket.subject, "status": "open"}


@router.get("/support/tickets")
async def driver_support_tickets(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.driver_id == driver.id)
        .order_by(SupportTicket.created_at.desc())
        .limit(50)
    )
    return {
        "data": [
            {
                "id": str(t.id),
                "subject": t.subject,
                "status": t.status.lower(),
                "priority": t.priority.lower(),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in result.scalars().all()
        ]
    }


@router.get("/support/tickets/{ticket_id}")
async def driver_support_ticket_detail(
    ticket_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    from app.models import SupportTicketReply

    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket or ticket.driver_id != driver.id:
        raise NotFoundException("Ticket not found")

    replies_result = await db.execute(
        select(SupportTicketReply)
        .where(SupportTicketReply.ticket_id == ticket.id)
        .order_by(SupportTicketReply.created_at.asc())
    )
    replies = list(replies_result.scalars().all())
    driver_name = f"{driver.first_name} {driver.last_name}".strip() or "You"
    status_key = ticket.status.lower()
    messages = [
        {
            "id": f"{ticket.id}-initial",
            "sender": driver_name,
            "sender_type": "driver",
            "message": ticket.description,
            "created_at": ticket.created_at.isoformat(),
        }
    ]
    for reply in replies:
        messages.append(
            {
                "id": str(reply.id),
                "sender": "Fast Bull Support" if reply.sender_type == "ADMIN" else driver_name,
                "sender_type": reply.sender_type.lower(),
                "message": reply.message,
                "created_at": reply.created_at.isoformat(),
            }
        )
    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "status": status_key,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "messages": messages,
    }


@router.post("/sos")
async def driver_trigger_sos(
    data: DriverSosRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    message = (data.message or "").strip() or "Driver triggered emergency SOS"
    ticket = SupportTicket(
        driver_id=driver.id,
        subject="SOS Emergency Alert",
        description=f"{message}\nLocation: {data.lat}, {data.lng}",
        status=SupportTicketStatus.OPEN.value,
        priority=SupportTicketPriority.URGENT.value,
    )
    db.add(ticket)
    await NotificationService(db).create_in_app(
        title="SOS Alert Sent",
        message="Emergency services and support have been notified with your location.",
        notification_type="SYSTEM",
        driver_id=driver.id,
        data={"lat": data.lat, "lng": data.lng, "ticket_id": str(ticket.id)},
    )
    admin_alert = Notification(
        title="Driver SOS Alert",
        message=f"{driver.first_name} {driver.last_name} triggered SOS at {data.lat}, {data.lng}",
        notification_type="ADMIN",
    )
    db.add(admin_alert)
    await db.flush()
    return {"success": True, "ticket_id": str(ticket.id)}


@router.get("/active-ride")
async def active_ride(driver: Annotated[Driver, Depends(get_current_driver)], db: AsyncSession = Depends(get_db)):
    ride = await RideRepository(db).get_active_ride_for_driver(driver.id)
    if not ride:
        return None
    loaded = await _load_driver_ride(db, ride.id)
    return _driver_active_ride_payload(loaded)


@router.get("/ride-history")
async def ride_history(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    repo = RideRepository(db)
    rides = await repo.get_driver_rides(driver.id, page, page_size)
    total = await repo.count([Ride.driver_id == driver.id])
    return {
        "items": [RideResponse.model_validate(r) for r in rides],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


class ArrivedRideRequest(BaseModel):
    ride_id: UUID


@router.post("/arrived-ride", response_model=RideResponse)
async def arrived_ride(
    data: ArrivedRideRequest,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    ride = await RideService(db).get_ride(data.ride_id)
    if ride.driver_id != driver.id:
        raise ForbiddenException("Access denied")
    ride = await RideService(db).driver_arrived(data.ride_id, driver.id)
    await manager.broadcast_ride(str(data.ride_id), {"event": "driver_arrived", "ride_id": str(data.ride_id)})
    loaded = await _load_driver_ride(db, data.ride_id)
    return _driver_active_ride_payload(loaded)


@router.get("/notifications")
async def driver_notifications(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    service = NotificationService(db)
    items, total, unread_count = await service.list_for_driver(driver.id, page, page_size)
    return {
        "data": [serialize_driver_notification(n) for n in items],
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "page_size": page_size,
    }


@router.put("/notifications/read-all")
async def mark_all_driver_notifications_read(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    updated = await NotificationService(db).mark_all_driver_notifications_read(driver.id)
    return {"updated": updated}


@router.put("/notifications/{notification_id}/read")
async def mark_driver_notification_read(
    notification_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    notification = await NotificationService(db).mark_driver_notification_read(notification_id, driver.id)
    return serialize_driver_notification(notification)


@router.get("/emergency-contacts")
async def list_emergency_contacts(
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    contacts = await DriverEmergencyContactService(db).list_for_driver(driver.id)
    return {"data": [contact_to_response(c).model_dump() for c in contacts]}


@router.post("/emergency-contacts", response_model=EmergencyContactResponse)
async def create_emergency_contact(
    data: EmergencyContactCreate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    contact = await DriverEmergencyContactService(db).create(driver.id, data)
    return contact_to_response(contact)


@router.put("/emergency-contacts/{contact_id}", response_model=EmergencyContactResponse)
async def update_emergency_contact(
    contact_id: UUID,
    data: EmergencyContactUpdate,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    contact = await DriverEmergencyContactService(db).update(driver.id, contact_id, data)
    return contact_to_response(contact)


@router.delete("/emergency-contacts/{contact_id}")
async def delete_emergency_contact(
    contact_id: UUID,
    driver: Annotated[Driver, Depends(get_current_driver)],
    db: AsyncSession = Depends(get_db),
):
    await DriverEmergencyContactService(db).delete(driver.id, contact_id)
    return {"success": True}
