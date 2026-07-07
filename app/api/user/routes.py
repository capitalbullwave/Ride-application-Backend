"""User Panel API — /api/v1/user/*"""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.user.dependencies import get_current_user
from app.api.user.service import UserApiService
from app.core.constants import RideStatus, SupportTicketPriority, SupportTicketStatus
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.core.logging import get_logger
from app.database.session import get_db
from app.models import Notification, Rating, Ride, SavedAddress, StudentPass, SubscriptionPlan, SupportTicket, SupportTicketReply, User, UserSubscription, VehicleType
from app.repositories.ride_repository import RideRepository
from app.repositories.user_repository import UserRepository
from app.schemas.payment import WalletTopUp, WalletTransactionResponse
from app.rides.schemas import RideBookRequest
from app.schemas.ride import RideDetailResponse, RideResponse
from app.services.driver_matching import DriverMatchingService
from app.services.payment_service import WalletService
from app.services.ride_service import RideService
from app.services.user_benefits_service import (
    get_user_ride_discount_percent,
    map_student_pass,
    map_subscription_plan,
)
from app.services.subscription_payment_service import (
    SubscriptionPaymentService,
    activate_user_subscription,
)
from app.services.wallet_payment_service import WalletPaymentService
from app.api.websocket.manager import manager

logger = get_logger(__name__)
from app.utils.phone import format_phone_display

router = APIRouter(tags=["User"])


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class SavedAddressCreate(BaseModel):
    label: str
    address_line: str
    latitude: float | None = None
    longitude: float | None = None
    is_default: bool = False


class BookRideRequest(BaseModel):
    pickup_address: str
    dropoff_address: str
    pickup_lat: float = 28.6328
    pickup_lng: float = 77.2167
    dropoff_lat: float = 28.4595
    dropoff_lng: float = 77.0266
    vehicle_category_id: str | None = None
    payment_method: str = "CASH"
    rental_hours: float | None = Field(default=None, ge=0)
    scheduled_at: datetime | None = None


class SupportRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=5)
    category: str | None = None


def _ticket_status_key(status: str) -> str:
    return {
        SupportTicketStatus.OPEN.value: "open",
        SupportTicketStatus.IN_PROGRESS.value: "in_progress",
        SupportTicketStatus.RESOLVED.value: "resolved",
        SupportTicketStatus.CLOSED.value: "closed",
    }.get(status, status.lower())


async def _load_ticket_replies(db: AsyncSession, ticket_id: UUID) -> list[SupportTicketReply]:
    result = await db.execute(
        select(SupportTicketReply)
        .where(SupportTicketReply.ticket_id == ticket_id)
        .order_by(SupportTicketReply.created_at.asc())
    )
    return list(result.scalars().all())


def _ticket_messages(ticket: SupportTicket, replies: list[SupportTicketReply], user_name: str) -> list[dict]:
    messages = [
        {
            "id": f"{ticket.id}-initial",
            "sender": user_name,
            "sender_type": "user",
            "message": ticket.description,
            "created_at": ticket.created_at.isoformat(),
        }
    ]
    for reply in replies:
        messages.append(
            {
                "id": str(reply.id),
                "sender": "Fast Bull Support" if reply.sender_type == "ADMIN" else user_name,
                "sender_type": reply.sender_type.lower(),
                "message": reply.message,
                "created_at": reply.created_at.isoformat(),
            }
        )
    return messages


class CancelRideRequest(BaseModel):
    ride_id: UUID
    reason: str | None = None


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)
    description: str = "Wallet top-up"


class WalletCheckoutRequest(BaseModel):
    amount: float = Field(..., gt=0, le=100000)


class WalletVerifyPayment(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def _address_response(address: SavedAddress) -> dict:
    return {
        "id": str(address.id),
        "label": address.label,
        "address_line": address.address,
        "latitude": address.lat,
        "longitude": address.lng,
        "is_default": address.is_default,
    }


def _user_to_profile(user: User, addresses: list[SavedAddress], total_rides: int = 0) -> dict:
    return {
        "id": str(user.id),
        "phone": format_phone_display(user.phone),
        "full_name": f"{user.first_name} {user.last_name}".strip(),
        "email": user.email,
        "profile_image_url": user.profile_photo,
        "emergency_contact_name": user.emergency_contact_name,
        "emergency_contact_phone": user.emergency_contact_phone,
        "total_rides": total_rides,
        "addresses": [_address_response(a) for a in addresses],
    }


def _ride_summary(ride: Ride) -> dict:
    return {
        "id": str(ride.id),
        "pickup_address": ride.pickup_address,
        "dropoff_address": ride.dropoff_address,
        "status": ride.status,
        "fare_estimate": ride.estimated_fare,
        "fare_final": ride.final_fare,
        "created_at": ride.created_at.isoformat(),
    }


def _serialize_vehicle_type(ride: Ride) -> dict | None:
    vt = ride.vehicle_type
    if vt is None:
        return None
    return {
        "id": str(vt.id),
        "name": vt.name,
        "slug": vt.slug,
    }


def _active_ride_summary(ride: Ride) -> dict:
    summary = _ride_summary(ride)
    summary["pickup_lat"] = ride.pickup_lat
    summary["pickup_lng"] = ride.pickup_lng
    summary["dropoff_lat"] = ride.dropoff_lat
    summary["dropoff_lng"] = ride.dropoff_lng
    if ride.driver:
        summary["driver"] = {
            "id": str(ride.driver.id),
            "name": f"{ride.driver.first_name} {ride.driver.last_name}".strip(),
            "phone": format_phone_display(ride.driver.phone),
            "rating": ride.driver.rating_avg,
        }
    if ride.vehicle:
        summary["vehicle_number"] = ride.vehicle.license_plate
    vehicle_type = _serialize_vehicle_type(ride)
    if vehicle_type:
        summary["vehicle_type"] = vehicle_type
        summary["vehicle_type_slug"] = vehicle_type["slug"]
        summary["vehicle_type_name"] = vehicle_type["name"]
    if ride.ride_otp and ride.status in (
        RideStatus.DRIVER_ASSIGNED.value,
        RideStatus.DRIVER_ARRIVED.value,
    ):
        summary["start_code"] = ride.ride_otp
    return summary


async def _active_ride_summary_enriched(db: AsyncSession, ride: Ride) -> dict:
    from app.models import DriverLocation

    summary = _active_ride_summary(ride)
    if ride.driver_id:
        loc_result = await db.execute(
            select(DriverLocation).where(DriverLocation.driver_id == ride.driver_id)
        )
        loc = loc_result.scalar_one_or_none()
        if loc:
            summary["driver_lat"] = loc.lat
            summary["driver_lng"] = loc.lng
    return summary


def _serialize_user_notification(notification: Notification) -> dict:
    raw_type = (notification.notification_type or "SYSTEM").upper()
    mapped_type = "ride" if raw_type == "RIDE" else raw_type.lower()
    return {
        "id": str(notification.id),
        "title": notification.title,
        "body": notification.message,
        "message": notification.message,
        "type": mapped_type,
        "notification_type": notification.notification_type,
        "is_read": notification.is_read,
        "read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
        "data": notification.data,
    }


@router.get("/profile")
async def get_profile(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SavedAddress).where(SavedAddress.user_id == user.id, SavedAddress.is_deleted == False)
    )
    ride_count = (
        await db.execute(
            select(func.count()).select_from(Ride).where(
                Ride.user_id == user.id,
                Ride.status == RideStatus.COMPLETED.value,
            )
        )
    ).scalar_one()
    return _user_to_profile(user, list(result.scalars().all()), int(ride_count or 0))


@router.put("/profile")
@router.patch("/profile")
async def update_profile(
    data: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    if data.full_name:
        parts = data.full_name.strip().split(" ", 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
    if data.email is not None:
        user.email = data.email
    if data.emergency_contact_name is not None:
        user.emergency_contact_name = data.emergency_contact_name
    if data.emergency_contact_phone is not None:
        user.emergency_contact_phone = data.emergency_contact_phone
    await UserRepository(db).update(user)
    result = await db.execute(select(SavedAddress).where(SavedAddress.user_id == user.id))
    ride_count = (
        await db.execute(
            select(func.count()).select_from(Ride).where(
                Ride.user_id == user.id,
                Ride.status == RideStatus.COMPLETED.value,
            )
        )
    ).scalar_one()
    return _user_to_profile(user, list(result.scalars().all()), int(ride_count or 0))


async def _list_user_addresses(user: User, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(SavedAddress).where(SavedAddress.user_id == user.id, SavedAddress.is_deleted == False)
    )
    return [_address_response(a) for a in result.scalars().all()]


@router.get("/profile/addresses")
async def list_profile_addresses(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    return await _list_user_addresses(user, db)


@router.post("/profile/addresses", status_code=201)
async def create_profile_address(
    data: SavedAddressCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    if data.is_default:
        existing = await db.execute(
            select(SavedAddress).where(
                SavedAddress.user_id == user.id,
                SavedAddress.is_deleted == False,
                SavedAddress.is_default == True,
            )
        )
        for row in existing.scalars().all():
            row.is_default = False

    address = SavedAddress(
        user_id=user.id,
        label=data.label,
        address=data.address_line,
        lat=data.latitude if data.latitude is not None else 0.0,
        lng=data.longitude if data.longitude is not None else 0.0,
        is_default=data.is_default,
    )
    db.add(address)
    await db.commit()
    await db.refresh(address)
    return _address_response(address)


@router.delete("/profile/addresses/{address_id}")
async def delete_profile_address(
    address_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedAddress).where(
            SavedAddress.id == address_id,
            SavedAddress.user_id == user.id,
            SavedAddress.is_deleted == False,
        )
    )
    address = result.scalar_one_or_none()
    if not address:
        raise NotFoundException("Address not found")
    address.soft_delete()
    await db.commit()
    return {"message": "Address deleted", "success": True}


@router.get("/saved-address")
async def saved_addresses(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    return await _list_user_addresses(user, db)


@router.post("/book-ride")
async def book_ride(
    data: BookRideRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    if not data.vehicle_category_id:
        vt_result = await db.execute(select(VehicleType).where(VehicleType.is_active == True).limit(1))
        vt = vt_result.scalar_one_or_none()
        if not vt:
            raise NotFoundException("No vehicle types available")
        vehicle_type_id = vt.id
    else:
        vehicle_type_id = UUID(data.vehicle_category_id)

    ride_data = RideBookRequest(
        pickup_address=data.pickup_address,
        pickup_lat=data.pickup_lat,
        pickup_lng=data.pickup_lng,
        dropoff_address=data.dropoff_address,
        dropoff_lat=data.dropoff_lat,
        dropoff_lng=data.dropoff_lng,
        vehicle_type_id=vehicle_type_id,
        payment_method=data.payment_method,
        rental_hours=data.rental_hours,
        scheduled_at=data.scheduled_at,
    )
    ride = await RideService(db).create_ride(user.id, ride_data)
    logger.info(
        "ride_book_requested",
        ride_id=str(ride.id),
        user_id=str(user.id),
        vehicle_type_id=str(vehicle_type_id),
        pickup_address=data.pickup_address,
        dropoff_address=data.dropoff_address,
        status=ride.status,
    )
    notified = await DriverMatchingService(db).dispatch_ride_to_online_drivers(ride, manager)
    logger.info(
        "ride_driver_search_dispatched",
        ride_id=str(ride.id),
        drivers_notified=notified,
    )
    await manager.broadcast_ride(str(ride.id), {
        "event": "ride_requested",
        "ride_id": str(ride.id),
        "status": ride.status,
        "drivers_notified": notified,
    })
    summary = _ride_summary(ride)
    summary["drivers_notified"] = notified
    return summary


@router.get("/rides")
async def list_rides(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
):
    repo = RideRepository(db)
    rides = await repo.get_user_rides(user.id, page, page_size, status)
    active = await repo.get_active_ride_for_user(user.id)
    active_summary = None
    if active:
        from sqlalchemy.orm import selectinload

        loaded = await db.execute(
            select(Ride)
            .options(
                selectinload(Ride.driver),
                selectinload(Ride.vehicle),
                selectinload(Ride.vehicle_type),
            )
            .where(Ride.id == active.id)
        )
        active_ride = loaded.scalar_one_or_none()
        if active_ride:
            active_summary = await _active_ride_summary_enriched(db, active_ride)
    return {
        "active": active_summary,
        "items": [_ride_summary(r) for r in rides],
        "page": page,
        "page_size": page_size,
    }


@router.get("/ride/{ride_id}")
async def get_ride(
    ride_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    ride = await RideService(db).get_ride(ride_id)
    if ride.user_id != user.id:
        raise ForbiddenException("Access denied")
    response = RideDetailResponse.model_validate(ride)
    if ride.driver:
        response.driver = {
            "id": str(ride.driver.id),
            "name": f"{ride.driver.first_name} {ride.driver.last_name}",
            "phone": ride.driver.phone,
            "rating": ride.driver.rating_avg,
        }
    return response


@router.post("/cancel-ride")
async def cancel_ride(
    data: CancelRideRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.driver_matching import DriverMatchingService

    ride_service = RideService(db)
    matching = DriverMatchingService(db)

    ride = await ride_service.get_ride(data.ride_id)
    if ride.user_id != user.id:
        raise ForbiddenException("Access denied")
    ride = await ride_service.cancel_ride(
        data.ride_id, "USER", data.reason or "Cancelled by user"
    )
    await matching.clear_ride_requests(data.ride_id)

    from app.notifications.service import NotificationService

    await NotificationService(db).close_all_ride_requests_for_ride(data.ride_id, "cancelled")

    extra_cancelled = await ride_service.crud.cancel_orphaned_search_rides(user.id)
    for ride_id in extra_cancelled:
        await matching.clear_ride_requests(ride_id)
        await NotificationService(db).close_all_ride_requests_for_ride(ride_id, "cancelled")

    await manager.broadcast_ride(str(data.ride_id), {
        "event": "ride_cancelled",
        "ride_id": str(data.ride_id),
        "reason": data.reason or "Cancelled by passenger",
    })
    if ride.driver_id:
        await manager.send_personal(
            str(ride.driver_id),
            {
                "event": "ride_cancelled",
                "ride_id": str(data.ride_id),
                "reason": data.reason or "Cancelled by passenger",
            },
        )
    return RideResponse.model_validate(ride)


@router.get("/wallet")
async def get_wallet(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    wallet = await WalletService(db).get_or_create_wallet(user_id=user.id)
    return {
        "balance": wallet.balance,
        "bonus_balance": 0.0,
        "referral_balance": 0.0,
        "total": wallet.balance,
    }


@router.get("/transactions")
async def get_transactions(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.models import WalletTransaction

    wallet = await WalletService(db).get_or_create_wallet(user_id=user.id)
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [WalletTransactionResponse.model_validate(t) for t in result.scalars().all()]


@router.post("/payment")
async def add_payment(
    data: PaymentRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    raise ValidationException(
        "Direct wallet credit is disabled. Use POST /user/wallet/checkout and Razorpay payment."
    )


@router.post("/wallet/checkout")
async def wallet_checkout(
    data: WalletCheckoutRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    service = WalletPaymentService(db)
    return await service.create_checkout(user, data.amount)


@router.post("/wallet/verify-payment")
async def wallet_verify_payment(
    data: WalletVerifyPayment,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    service = WalletPaymentService(db)
    return await service.verify_and_credit(
        user,
        razorpay_order_id=data.razorpay_order_id,
        razorpay_payment_id=data.razorpay_payment_id,
        razorpay_signature=data.razorpay_signature,
    )


@router.get("/notifications")
async def notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize_user_notification(n) for n in result.scalars().all()]


@router.put("/notifications/read-all")
async def mark_all_user_notifications_read(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import update

    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.flush()
    return {"updated": int(result.rowcount or 0)}


@router.put("/notifications/{notification_id}/read")
async def mark_user_notification_read(
    notification_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise NotFoundException("Notification not found")
    notification.is_read = True
    await db.flush()
    return {"id": str(notification.id), "is_read": True}


class RateRideRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


@router.post("/ride/{ride_id}/rate")
async def rate_ride(
    ride_id: UUID,
    data: RateRideRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.services.rating_service import RatingService

    return await RatingService(db).rate_driver(ride_id, user, data.rating, data.comment)


class RideChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


@router.get("/ride/{ride_id}/messages")
async def list_ride_messages_user(
    ride_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.rides.chat_service import RideChatService

    ride = await db.get(Ride, ride_id)
    if not ride:
        raise NotFoundException("Ride not found")
    if ride.user_id != user.id:
        raise ForbiddenException("Access denied")
    service = RideChatService(db)
    return {"success": True, "data": await service.list_messages(ride_id)}


@router.post("/ride/{ride_id}/messages")
async def send_ride_message_user(
    ride_id: UUID,
    data: RideChatMessageRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from app.rides.chat_service import RideChatService

    service = RideChatService(db)
    message = await service.send_message(
        ride_id,
        sender_id=user.id,
        sender_type="user",
        message=data.message,
    )
    await db.commit()
    return {"success": True, "data": message}


@router.post("/support")
async def create_support(
    data: SupportRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    subject = data.subject.strip()
    if data.category and not subject.startswith("["):
        subject = f"[{data.category}] {subject}"

    ticket = SupportTicket(
        user_id=user.id,
        subject=subject,
        description=data.message.strip(),
        status=SupportTicketStatus.OPEN.value,
        priority=SupportTicketPriority.MEDIUM.value,
    )
    db.add(ticket)
    await db.flush()
    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "status": _ticket_status_key(ticket.status),
        "created_at": ticket.created_at.isoformat(),
    }


@router.get("/support/tickets")
async def list_user_support_tickets(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.created_at.desc())
        .limit(50)
    )
    return {
        "data": [
            {
                "id": str(t.id),
                "subject": t.subject,
                "status": _ticket_status_key(t.status),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in result.scalars().all()
        ]
    }


@router.get("/support/tickets/{ticket_id}")
async def get_user_support_ticket(
    ticket_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket or ticket.user_id != user.id:
        raise NotFoundException("Ticket not found")

    replies = await _load_ticket_replies(db, ticket.id)
    user_name = f"{user.first_name} {user.last_name}".strip() or "You"
    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "status": _ticket_status_key(ticket.status),
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "messages": _ticket_messages(ticket, replies, user_name),
    }


@router.get("/dashboard")
async def user_dashboard(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    return await UserApiService(db).home_dashboard(user)


@router.get("/ride/{ride_id}/driver")
async def ride_driver(ride_id: UUID, user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    ride = await RideService(db).get_ride(ride_id)
    if ride.user_id != user.id or not ride.driver:
        raise NotFoundException("Driver not assigned")
    driver = ride.driver
    return {
        "id": str(driver.id),
        "name": f"{driver.first_name} {driver.last_name}".strip(),
        "phone": format_phone_display(driver.phone),
        "rating": driver.rating_avg,
        "vehicle_number": ride.vehicle.license_plate if ride.vehicle else "",
        "photo_url": driver.profile_photo,
    }


class StudentPassSubmit(BaseModel):
    aadhar_number: str = Field(..., min_length=12, max_length=12)
    college_name: str = Field(..., min_length=2, max_length=200)
    aadhar_photo: str | None = None
    student_id_photo: str | None = None


class SubscriptionSelect(BaseModel):
    plan_slug: str = Field(..., min_length=2, max_length=50)


class SubscriptionCheckoutRequest(BaseModel):
    plan_slug: str = Field(..., min_length=2, max_length=50)


class SubscriptionVerifyPayment(BaseModel):
    plan_slug: str = Field(..., min_length=2, max_length=50)
    razorpay_order_id: str = Field(..., min_length=5, max_length=100)
    razorpay_payment_id: str = Field(..., min_length=5, max_length=100)
    razorpay_signature: str = Field(..., min_length=5, max_length=255)


@router.get("/student-pass")
async def get_student_pass(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    record = await db.scalar(select(StudentPass).where(StudentPass.user_id == user.id))
    if not record:
        return {"application": None}
    return {"application": map_student_pass(record)}


@router.post("/student-pass")
async def submit_student_pass(
    data: StudentPassSubmit,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    if not data.aadhar_number.isdigit():
        raise ValidationException("Aadhar number must contain 12 digits")
    if not data.aadhar_photo or not data.student_id_photo:
        raise ValidationException("Aadhar photo and student ID photo are required")

    existing = await db.scalar(select(StudentPass).where(StudentPass.user_id == user.id))
    aadhar_url = persist_user_image(data.aadhar_photo, str(user.id), "aadhar")
    student_id_url = persist_user_image(data.student_id_photo, str(user.id), "student_id")

    if existing:
        if existing.status == "APPROVED":
            raise ValidationException("Your student pass is already verified")
        existing.aadhar_number = data.aadhar_number
        existing.college_name = data.college_name.strip()
        existing.aadhar_photo_url = aadhar_url
        existing.student_id_photo_url = student_id_url
        existing.status = "PENDING"
        existing.rejection_reason = None
        existing.verified_at = None
        existing.verified_by_id = None
        record = existing
    else:
        record = StudentPass(
            user_id=user.id,
            aadhar_number=data.aadhar_number,
            college_name=data.college_name.strip(),
            aadhar_photo_url=aadhar_url,
            student_id_photo_url=student_id_url,
            status="PENDING",
            discount_percent=20.0,
        )
        db.add(record)

    await db.commit()
    await db.refresh(record)
    return {"application": map_student_pass(record), "message": "Application submitted for verification"}


@router.get("/subscription-plans")
async def list_subscription_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order.asc())
    )
    return {"plans": [map_subscription_plan(plan) for plan in result.scalars().all()]}


@router.get("/subscription")
async def get_user_subscription(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload

    sub = await db.scalar(
        select(UserSubscription)
        .options(selectinload(UserSubscription.plan))
        .where(UserSubscription.user_id == user.id, UserSubscription.status == "ACTIVE")
    )
    if not sub or not sub.plan:
        free_plan = await db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.slug == "free"))
        return {
            "subscription": {
                "plan": map_subscription_plan(free_plan) if free_plan else None,
                "status": "active",
            }
        }
    return {
        "subscription": {
            "plan": map_subscription_plan(sub.plan),
            "status": sub.status.lower(),
            "started_at": sub.started_at.isoformat(),
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        }
    }


@router.post("/subscription/checkout")
async def subscription_checkout(
    data: SubscriptionCheckoutRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    service = SubscriptionPaymentService(db)
    return await service.create_checkout(user, data.plan_slug)


@router.post("/subscription/verify-payment")
async def subscription_verify_payment(
    data: SubscriptionVerifyPayment,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    service = SubscriptionPaymentService(db)
    return await service.verify_and_activate(
        user,
        plan_slug=data.plan_slug,
        razorpay_order_id=data.razorpay_order_id,
        razorpay_payment_id=data.razorpay_payment_id,
        razorpay_signature=data.razorpay_signature,
    )


@router.post("/subscription")
async def select_subscription_plan(
    data: SubscriptionSelect,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    plan = await db.scalar(
        select(SubscriptionPlan).where(
            SubscriptionPlan.slug == data.plan_slug,
            SubscriptionPlan.is_active.is_(True),
        )
    )
    if not plan:
        raise NotFoundException("Subscription plan not found")
    if plan.price > 0:
        raise ValidationException("Paid plans require Razorpay payment. Use subscription checkout.")

    sub = await activate_user_subscription(db, user.id, plan)
    await db.commit()
    await db.refresh(sub, attribute_names=["plan"])
    return {
        "subscription": {
            "plan": map_subscription_plan(plan),
            "status": "active",
            "started_at": sub.started_at.isoformat(),
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        },
        "message": f"{plan.name} plan activated",
    }
