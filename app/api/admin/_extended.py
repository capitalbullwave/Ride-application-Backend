"""Admin extended routes — dashboard, finance, settings."""
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.admin._core import _map_driver, _map_ride
from app.auth.dependencies import get_current_admin
from app.core.constants import DriverStatus, KYCStatus, RideStatus, SupportTicketPriority, SupportTicketStatus
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.database.session import get_db
from app.services.image_storage import persist_vehicle_type_image
from app.services.user_benefits_service import map_student_pass, map_subscription_plan
from app.commission.schemas import (
    VehicleCommissionSettingsResponse,
    VehicleCommissionSettingsUpdate,
)
from app.services.commission_service import CommissionService
from app.notifications.service import NotificationService
from app.subscriptions.models import StudentPass, SubscriptionPlan, UserSubscription
from app.models import (
    AdminUser,
    AppSetting,
    Driver,
    Notification,
    PromoCode,
    Ride,
    SupportTicket,
    SupportTicketReply,
    User,
    Vehicle,
    VehicleType,
    Wallet,
    WalletTransaction,
)

router = APIRouter(tags=["Admin"])

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULT_SETTINGS = {
    "appName": "Bull Wave Rides",
    "logo": "",
    "contactEmail": "support@ridebook.com",
    "contactPhone": "+91 98765 43210",
    "googleMapsApiKey": "",
    "firebaseConfig": "",
    "razorpayKey": "",
    "stripeKey": "",
    "driverCommission": 20,
    "platformFee": 5,
}

DEFAULT_FAQS = [
    {
        "category": "Rides",
        "question": "How do I book a ride?",
        "answer": "Open the app, enter pickup and drop locations, choose a vehicle type, and confirm your booking.",
    },
    {
        "category": "Payments",
        "question": "What payment methods are supported?",
        "answer": "You can pay using cash, wallet, UPI, or card depending on availability in your city.",
    },
    {
        "category": "Safety",
        "question": "How do I use SOS?",
        "answer": "During an active ride, tap the SOS button to alert our emergency response team with your live location.",
    },
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_start() -> datetime:
    now = _utc_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> datetime:
    now = _utc_now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _relative_time(dt: datetime) -> str:
    diff = _utc_now() - dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else _utc_now() - dt
    minutes = int(diff.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    return f"{hours // 24} days ago"


def _vehicle_slug(name: str) -> str:
    return name.lower().strip().replace(" ", "-")


def _vehicle_image_url(icon: str | None) -> str | None:
    if not icon:
        return None
    if icon.startswith(("/", "http://", "https://")):
        return icon
    return None


def _vehicle_icon_type(icon: str | None) -> str:
    if not icon or icon.startswith(("/", "http://", "https://")):
        return "car"
    return icon


def _map_vehicle_type(vt: VehicleType) -> dict:
    slug = vt.slug or _vehicle_slug(vt.name)
    image_url = _vehicle_image_url(vt.icon)
    return {
        "id": str(vt.id),
        "type": slug.replace("-", "_"),
        "slug": slug,
        "name": vt.name,
        "description": vt.description,
        "baseFare": vt.base_fare,
        "perKmFare": vt.per_km_rate,
        "includedDistanceKm": vt.included_distance_km,
        "includedHours": vt.included_hours,
        "perHourRate": vt.per_hour_rate,
        "waitingCharge": vt.waiting_charge_per_min,
        "cancellationCharge": vt.cancellation_charge,
        "surgeMultiplier": 1.5,
        "isActive": vt.is_active,
        "icon": _vehicle_icon_type(vt.icon),
        "imageUrl": image_url,
        "capacity": vt.capacity,
        "serviceGroup": vt.service_group or "ride",
        "driverCommissionPercentage": float(
            vt.driver_commission_percentage
            if vt.driver_commission_percentage is not None
            else CommissionService.DEFAULT_COMMISSION_PERCENTAGE
        ),
    }


def _map_coupon(p: PromoCode) -> dict:
    now = _utc_now()
    status = "active"
    if not p.is_active:
        status = "disabled"
    elif p.valid_until.replace(tzinfo=timezone.utc) < now:
        status = "expired"
    discount_type = "percentage" if p.discount_type.upper() == "PERCENTAGE" else "flat"
    return {
        "id": str(p.id),
        "code": p.code,
        "discountType": discount_type,
        "discountValue": p.discount_value,
        "maxDiscount": p.max_discount or p.discount_value,
        "expiryDate": p.valid_until.date().isoformat(),
        "usageLimit": p.max_uses,
        "usedCount": p.used_count,
        "status": status,
        "createdAt": p.created_at.date().isoformat(),
    }


def _map_support_ticket(
    ticket: SupportTicket,
    user: User | None = None,
    driver: Driver | None = None,
    replies: list[SupportTicketReply] | None = None,
) -> dict:
    if user:
        user_type = "user"
        user_id = str(user.id)
        user_name = f"{user.first_name} {user.last_name}".strip()
    elif driver:
        user_type = "driver"
        user_id = str(driver.id)
        user_name = f"{driver.first_name} {driver.last_name}".strip()
    else:
        user_type = "user"
        user_id = str(ticket.user_id or ticket.driver_id or "")
        user_name = "Unknown"

    status_map = {
        SupportTicketStatus.OPEN.value: "open",
        SupportTicketStatus.IN_PROGRESS.value: "in_progress",
        SupportTicketStatus.RESOLVED.value: "resolved",
        SupportTicketStatus.CLOSED.value: "closed",
    }
    messages = [
        {
            "id": f"{ticket.id}-initial",
            "sender": user_name,
            "senderType": user_type,
            "message": ticket.description,
            "timestamp": ticket.created_at.isoformat(),
        }
    ]
    for reply in replies or []:
        messages.append(
            {
                "id": str(reply.id),
                "sender": "Bull Wave Rides Support" if reply.sender_type == "ADMIN" else user_name,
                "senderType": reply.sender_type.lower(),
                "message": reply.message,
                "timestamp": reply.created_at.isoformat(),
            }
        )

    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "description": ticket.description,
        "userType": user_type,
        "userId": user_id,
        "userName": user_name,
        "status": status_map.get(ticket.status, ticket.status.lower()),
        "priority": ticket.priority.lower(),
        "createdAt": ticket.created_at.isoformat(),
        "updatedAt": ticket.updated_at.isoformat(),
        "messages": messages,
    }


async def _ticket_replies(db: AsyncSession, ticket_id: UUID) -> list[SupportTicketReply]:
    result = await db.execute(
        select(SupportTicketReply)
        .where(SupportTicketReply.ticket_id == ticket_id)
        .order_by(SupportTicketReply.created_at.asc())
    )
    return list(result.scalars().all())


def _map_wallet_transaction(tx: WalletTransaction, wallet: Wallet) -> dict:
    tx_type = tx.transaction_type.lower()
    if tx_type == "admin_adjustment":
        tx_type = "debit"
    return {
        "id": str(tx.id),
        "type": tx_type,
        "amount": tx.amount,
        "description": tx.description,
        "userId": str(wallet.user_id) if wallet.user_id else None,
        "driverId": str(wallet.driver_id) if wallet.driver_id else None,
        "rideId": tx.reference_id if tx.reference_type == "ride" else None,
        "status": "completed",
        "date": tx.created_at.isoformat(),
        "paymentMethod": None,
    }


async def _get_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(AppSetting))
    settings = {row.key: row.value for row in result.scalars().all()}
    merged = dict(DEFAULT_SETTINGS)
    commission = await CommissionService(db).get_vehicle_settings_response()
    merged["driverCommission"] = commission.default_commission_percentage
    merged["platformFee"] = round(100 - commission.default_commission_percentage, 2)
    merged["commissionMode"] = "per_vehicle"
    key_map = {
        "app_name": "appName",
        "contact_email": "contactEmail",
        "contact_phone": "contactPhone",
        "google_maps_api_key": "googleMapsApiKey",
        "firebase_config": "firebaseConfig",
        "razorpay_key": "razorpayKey",
        "stripe_key": "stripeKey",
        "logo": "logo",
    }
    for db_key, front_key in key_map.items():
        if db_key in settings:
            merged[front_key] = settings[db_key]
    return merged


async def _ride_with_names(db: AsyncSession, ride: Ride) -> dict:
    user = await db.get(User, ride.user_id)
    driver = await db.get(Driver, ride.driver_id) if ride.driver_id else None
    mapped = _map_ride(ride, user, driver)
    if ride.vehicle_type_id:
        vt = await db.get(VehicleType, ride.vehicle_type_id)
        if vt:
            mapped["vehicleType"] = vt.name.lower().replace(" ", "_")
    return mapped


@router.get("/dashboard/stats")
async def dashboard_stats(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    total_users = (await db.execute(select(func.count()).select_from(User).where(User.is_deleted == False))).scalar_one()
    total_drivers = (await db.execute(select(func.count()).select_from(Driver).where(Driver.is_deleted == False))).scalar_one()
    active_drivers = (
        await db.execute(
            select(func.count()).select_from(Driver).where(
                Driver.is_deleted == False,
                Driver.status.in_([DriverStatus.ONLINE.value, DriverStatus.ON_RIDE.value, DriverStatus.BUSY.value]),
            )
        )
    ).scalar_one()
    active_rides = (
        await db.execute(
            select(func.count()).select_from(Ride).where(
                Ride.status.in_([
                    RideStatus.REQUESTED.value,
                    RideStatus.SEARCHING.value,
                    RideStatus.ACCEPTED.value,
                    RideStatus.DRIVER_ARRIVED.value,
                    RideStatus.STARTED.value,
                ])
            )
        )
    ).scalar_one()
    completed_rides = (
        await db.execute(select(func.count()).select_from(Ride).where(Ride.status == RideStatus.COMPLETED.value))
    ).scalar_one()
    cancelled_rides = (
        await db.execute(select(func.count()).select_from(Ride).where(Ride.status == RideStatus.CANCELLED.value))
    ).scalar_one()

    today = _today_start()
    month = _month_start()
    today_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= today,
            )
        )
    ).scalar_one()
    monthly_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= month,
            )
        )
    ).scalar_one()

    total_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
            )
        )
    ).scalar_one()

    driver_earnings_today = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.driver_earning), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= today,
            )
        )
    ).scalar_one()

    company_earnings_today = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.company_earning), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= today,
            )
        )
    ).scalar_one()

    total_commission_paid = (
        await db.execute(
            select(func.coalesce(func.sum(Ride.driver_earning), 0.0)).where(
                Ride.status == RideStatus.COMPLETED.value,
            )
        )
    ).scalar_one()

    commission_settings = await CommissionService(db).get_vehicle_settings_response()

    return {
        "totalUsers": total_users,
        "totalDrivers": total_drivers,
        "activeDrivers": active_drivers,
        "activeRides": active_rides,
        "completedRides": completed_rides,
        "cancelledRides": cancelled_rides,
        "todayRevenue": float(today_revenue or 0),
        "monthlyRevenue": float(monthly_revenue or 0),
        "totalRevenue": float(total_revenue or 0),
        "driverEarningsToday": float(driver_earnings_today or 0),
        "companyEarningsToday": float(company_earnings_today or 0),
        "totalCommissionPaid": float(total_commission_paid or 0),
        "driverCommissionPercentage": commission_settings.default_commission_percentage,
        "commissionMode": "per_vehicle",
    }


@router.get("/dashboard/charts")
async def dashboard_charts(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    now = _utc_now()
    ride_booking = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day + timedelta(days=1)
        count = (
            await db.execute(
                select(func.count()).select_from(Ride).where(Ride.created_at >= day, Ride.created_at < next_day)
            )
        ).scalar_one()
        ride_booking.append({"name": DAY_NAMES[day.weekday()], "rides": count, "value": count})

    revenue = []
    for i in range(5, -1, -1):
        month_dt = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if i > 0:
            next_month = (month_dt + timedelta(days=32)).replace(day=1)
        else:
            next_month = now.replace(day=1) + timedelta(days=32)
            next_month = next_month.replace(day=1)
        rev = (
            await db.execute(
                select(func.coalesce(func.sum(Ride.final_fare), 0.0)).where(
                    Ride.status == RideStatus.COMPLETED.value,
                    Ride.completed_at >= month_dt,
                    Ride.completed_at < next_month,
                )
            )
        ).scalar_one()
        revenue.append({"name": MONTH_NAMES[month_dt.month - 1], "revenue": float(rev or 0), "value": float(rev or 0)})

    total_users = (await db.execute(select(func.count()).select_from(User).where(User.is_deleted == False))).scalar_one()
    total_drivers = (await db.execute(select(func.count()).select_from(Driver).where(Driver.is_deleted == False))).scalar_one()

    user_growth = []
    driver_growth = []
    for i in range(5, -1, -1):
        month_dt = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        user_count = (
            await db.execute(
                select(func.count()).select_from(User).where(User.is_deleted == False, User.created_at <= month_dt + timedelta(days=31))
            )
        ).scalar_one()
        driver_count = (
            await db.execute(
                select(func.count()).select_from(Driver).where(Driver.is_deleted == False, Driver.created_at <= month_dt + timedelta(days=31))
            )
        ).scalar_one()
        label = MONTH_NAMES[month_dt.month - 1]
        user_growth.append({"name": label, "users": user_count, "value": user_count})
        driver_growth.append({"name": label, "drivers": driver_count, "value": driver_count})

    if total_users == 0:
        user_growth[-1]["users"] = 0
        user_growth[-1]["value"] = 0
    if total_drivers == 0:
        driver_growth[-1]["drivers"] = 0
        driver_growth[-1]["value"] = 0

    return {
        "rideBooking": ride_booking,
        "revenue": revenue,
        "userGrowth": user_growth,
        "driverGrowth": driver_growth,
    }


@router.get("/dashboard/activities")
async def dashboard_activities(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    activities = []

    rides = (
        await db.execute(select(Ride).order_by(Ride.created_at.desc()).limit(5))
    ).scalars().all()
    for ride in rides:
        user = await db.get(User, ride.user_id)
        name = f"{user.first_name} {user.last_name}" if user else "User"
        activities.append({
            "id": f"ride-{ride.id}",
            "type": "ride_request" if ride.status in (RideStatus.REQUESTED.value, RideStatus.SEARCHING.value) else "ongoing_ride",
            "title": "New Ride Request" if ride.status in (RideStatus.REQUESTED.value, RideStatus.SEARCHING.value) else "Ongoing Ride",
            "description": f"{name} — {ride.pickup_address} to {ride.dropoff_address}",
            "timestamp": _relative_time(ride.created_at),
            "status": ride.status.lower(),
        })

    users = (
        await db.execute(select(User).where(User.is_deleted == False).order_by(User.created_at.desc()).limit(3))
    ).scalars().all()
    for user in users:
        activities.append({
            "id": f"user-{user.id}",
            "type": "registration",
            "title": "New User Registration",
            "description": f"{user.first_name} {user.last_name} registered with {user.phone}",
            "timestamp": _relative_time(user.created_at),
        })

    online = (
        await db.execute(
            select(Driver).where(Driver.status == DriverStatus.ONLINE.value, Driver.is_deleted == False).limit(3)
        )
    ).scalars().all()
    for driver in online:
        activities.append({
            "id": f"driver-{driver.id}",
            "type": "driver_online",
            "title": "Driver Online",
            "description": f"{driver.first_name} {driver.last_name} is now online",
            "timestamp": _relative_time(driver.updated_at),
            "status": "online",
        })

    return activities[:10]


@router.get("/dashboard/online-drivers")
async def online_drivers(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Driver)
        .where(
            Driver.is_deleted == False,
            Driver.status.in_([DriverStatus.ONLINE.value, DriverStatus.ON_RIDE.value]),
        )
        .limit(10)
    )
    drivers = result.scalars().all()
    items = []
    for d in drivers:
        vehicle_result = await db.execute(select(Vehicle).where(Vehicle.driver_id == d.id).limit(1))
        vehicle = vehicle_result.scalar_one_or_none()
        mapped = _map_driver(d, vehicle)
        items.append({"id": mapped["id"], "name": mapped["name"], "vehicleType": mapped["vehicleType"], "status": mapped["status"]})
    return items


@router.get("/rides")
async def list_rides(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(Ride)
    if status and status != "all":
        status_map = {
            "requested": [RideStatus.REQUESTED.value, RideStatus.SEARCHING.value],
            "driver_assigned": [RideStatus.ACCEPTED.value],
            "driver_arrived": [RideStatus.DRIVER_ARRIVED.value],
            "ride_started": [RideStatus.STARTED.value],
            "ride_completed": [RideStatus.COMPLETED.value],
            "cancelled": [RideStatus.CANCELLED.value],
        }
        if status in status_map:
            query = query.where(Ride.status.in_(status_map[status]))
    if search:
        term = f"%{search}%"
        query = query.where(or_(Ride.pickup_address.ilike(term), Ride.dropoff_address.ilike(term)))

    count_query = select(func.count()).select_from(Ride)
    if status and status != "all":
        status_map = {
            "requested": [RideStatus.REQUESTED.value, RideStatus.SEARCHING.value],
            "driver_assigned": [RideStatus.ACCEPTED.value],
            "driver_arrived": [RideStatus.DRIVER_ARRIVED.value],
            "ride_started": [RideStatus.STARTED.value],
            "ride_completed": [RideStatus.COMPLETED.value],
            "cancelled": [RideStatus.CANCELLED.value],
        }
        if status in status_map:
            count_query = count_query.where(Ride.status.in_(status_map[status]))
    if search:
        term = f"%{search}%"
        count_query = count_query.where(or_(Ride.pickup_address.ilike(term), Ride.dropoff_address.ilike(term)))

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query.order_by(Ride.created_at.desc()).offset((page - 1) * limit).limit(limit))
    rides = result.scalars().all()
    items = [await _ride_with_names(db, r) for r in rides]
    return {"items": items, "total": total, "page": page, "limit": limit, "total_pages": max(1, (total + limit - 1) // limit)}


@router.get("/rides/export")
async def export_rides(admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Ride).order_by(Ride.created_at.desc()).limit(5000))
    rides = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "User", "Driver", "Pickup", "Drop", "Fare", "Status", "Date"])
    for ride in rides:
        mapped = await _ride_with_names(db, ride)
        writer.writerow([
            mapped["id"], mapped["userName"], mapped.get("driverName", ""),
            mapped["pickupLocation"], mapped["dropLocation"], mapped["fare"],
            mapped["status"], mapped["date"],
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rides.csv"},
    )


@router.get("/rides/{ride_id}")
async def get_ride(
    ride_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    ride = await db.get(Ride, ride_id)
    if not ride:
        raise NotFoundException("Ride not found")
    return await _ride_with_names(db, ride)


@router.get("/rides/{ride_id}/messages")
async def list_ride_messages_admin(
    ride_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.rides.chat_service import RideChatService

    ride = await db.get(Ride, ride_id)
    if not ride:
        raise NotFoundException("Ride not found")
    service = RideChatService(db)
    return {"success": True, "data": await service.list_messages(ride_id)}


@router.get("/users/export")
async def export_users(admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.is_deleted == False).order_by(User.created_at.desc()))
    users = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Email", "Phone", "Status", "Registered"])
    for u in users:
        writer.writerow([str(u.id), f"{u.first_name} {u.last_name}", u.email, u.phone, "active" if u.is_active else "blocked", u.created_at.date()])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=users.csv"})


@router.get("/drivers/export")
async def export_drivers(admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Driver).where(Driver.is_deleted == False).order_by(Driver.created_at.desc()))
    drivers = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Email", "Phone", "Status", "KYC", "Joined"])
    for d in drivers:
        writer.writerow([str(d.id), f"{d.first_name} {d.last_name}", d.email, d.phone, d.status, d.kyc_status, d.created_at.date()])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=drivers.csv"})


@router.get("/finance/transactions")
async def finance_transactions(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    type: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    query = (
        select(WalletTransaction, Wallet)
        .join(Wallet, WalletTransaction.wallet_id == Wallet.id)
        .order_by(WalletTransaction.created_at.desc())
    )
    result = await db.execute(query.offset((page - 1) * limit).limit(limit))
    rows = result.all()
    items = [_map_wallet_transaction(tx, wallet) for tx, wallet in rows]
    if type and type != "all":
        items = [i for i in items if i["type"] == type]
    return {"items": items, "total": len(items)}


@router.get("/vehicle-categories")
async def list_vehicle_categories(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VehicleType).order_by(VehicleType.name))
    return [_map_vehicle_type(vt) for vt in result.scalars().all()]


async def create_vehicle_category(
    data: dict,
    admin: AdminUser,
    db: AsyncSession,
):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationException("Vehicle name is required")

    slug = (data.get("slug") or _vehicle_slug(name)).strip().lower().replace(" ", "-")
    existing = await db.execute(
        select(VehicleType).where(or_(VehicleType.name == name, VehicleType.slug == slug))
    )
    if existing.scalar_one_or_none():
        raise ConflictException("A vehicle category with this name already exists")

    base_fare = float(data.get("baseFare", 25))
    per_km_rate = float(data.get("perKmFare", 10))
    service_group = (data.get("serviceGroup") or "ride").strip().lower()

    vt = VehicleType(
        name=name,
        slug=slug,
        description=data.get("description"),
        icon=data.get("icon", "car"),
        base_fare=base_fare,
        per_km_rate=per_km_rate,
        per_minute_rate=float(data.get("perMinuteRate", 2)),
        waiting_charge_per_min=float(data.get("waitingCharge", 2)),
        included_distance_km=float(data.get("includedDistanceKm", 2)),
        included_hours=float(data.get("includedHours", 4 if service_group == "rental" else 0)),
        per_hour_rate=float(data.get("perHourRate", 50 if service_group == "rental" else 0)),
        minimum_fare=float(data.get("minimumFare", base_fare)),
        cancellation_charge=float(
            data.get("cancellationCharge", max(base_fare, 20.0))
        ),
        service_group=service_group,
        capacity=int(data.get("capacity", 4)),
        is_active=data.get("isActive", True),
    )
    db.add(vt)
    await db.flush()

    image_payload = data.get("image") or data.get("imageUrl")
    if image_payload:
        stored = persist_vehicle_type_image(image_payload, str(vt.id))
        if stored:
            vt.icon = stored

    await db.flush()
    return _map_vehicle_type(vt)


@router.patch("/vehicle-categories/{category_id}")
async def update_vehicle_category(
    category_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    vt = await db.get(VehicleType, category_id)
    if not vt:
        raise NotFoundException("Vehicle category not found")
    field_map = {
        "baseFare": "base_fare",
        "perKmFare": "per_km_rate",
        "waitingCharge": "waiting_charge_per_min",
        "cancellationCharge": "cancellation_charge",
        "minimumFare": "minimum_fare",
        "includedDistanceKm": "included_distance_km",
        "includedHours": "included_hours",
        "perHourRate": "per_hour_rate",
        "serviceGroup": "service_group",
        "isActive": "is_active",
        "name": "name",
        "description": "description",
        "icon": "icon",
        "capacity": "capacity",
    }
    image_payload = data.get("image") or data.get("imageUrl")
    for front_key, db_key in field_map.items():
        if front_key in data:
            if (
                front_key == "icon"
                and _vehicle_image_url(vt.icon)
                and not image_payload
            ):
                continue
            setattr(vt, db_key, data[front_key])
    if "name" in data:
        vt.slug = _vehicle_slug(vt.name)
    if image_payload:
        stored = persist_vehicle_type_image(image_payload, str(vt.id))
        if stored:
            vt.icon = stored
    await db.flush()
    return _map_vehicle_type(vt)


async def delete_vehicle_category(
    category_id: UUID,
    admin: AdminUser,
    db: AsyncSession,
):
    vt = await db.get(VehicleType, category_id)
    if not vt:
        raise NotFoundException("Vehicle category not found")

    ride_count = await db.scalar(
        select(func.count()).select_from(Ride).where(Ride.vehicle_type_id == category_id)
    )
    vehicle_count = await db.scalar(
        select(func.count()).select_from(Vehicle).where(Vehicle.vehicle_type_id == category_id)
    )
    if ride_count or vehicle_count:
        vt.is_active = False
        await db.flush()
        return {"success": True, "deactivated": True}

    await db.delete(vt)
    await db.flush()
    return {"success": True, "deactivated": False}


@router.get("/coupons")
async def list_coupons(admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return [_map_coupon(p) for p in result.scalars().all()]


@router.post("/coupons")
async def create_coupon(
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    now = _utc_now()
    promo = PromoCode(
        code=data["code"].upper(),
        discount_type="PERCENTAGE" if data.get("discountType") == "percentage" else "FIXED",
        discount_value=float(data["discountValue"]),
        max_discount=float(data.get("maxDiscount", data["discountValue"])),
        max_uses=int(data.get("usageLimit", 100)),
        valid_from=now,
        valid_until=datetime.fromisoformat(data["expiryDate"]).replace(tzinfo=timezone.utc),
        is_active=data.get("status", "active") == "active",
    )
    db.add(promo)
    await db.flush()
    return _map_coupon(promo)


@router.patch("/coupons/{coupon_id}")
async def update_coupon(
    coupon_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    promo = await db.get(PromoCode, coupon_id)
    if not promo:
        raise NotFoundException("Coupon not found")
    if "code" in data and data["code"]:
        promo.code = str(data["code"]).upper()
    if "discountType" in data:
        promo.discount_type = (
            "PERCENTAGE" if data["discountType"] == "percentage" else "FIXED"
        )
    if "status" in data:
        promo.is_active = data["status"] == "active"
    if "discountValue" in data:
        promo.discount_value = float(data["discountValue"])
    if "maxDiscount" in data:
        promo.max_discount = float(data["maxDiscount"])
    if "usageLimit" in data:
        promo.max_uses = int(data["usageLimit"])
    if "expiryDate" in data and data["expiryDate"]:
        expiry = datetime.fromisoformat(str(data["expiryDate"]))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        promo.valid_until = expiry
    await db.flush()
    return _map_coupon(promo)


@router.delete("/coupons/{coupon_id}")
async def delete_coupon(
    coupon_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    promo = await db.get(PromoCode, coupon_id)
    if not promo:
        raise NotFoundException("Coupon not found")
    if int(promo.used_count or 0) > 0:
        promo.is_active = False
        await db.flush()
        return {"success": True, "deactivated": True}
    await db.delete(promo)
    await db.flush()
    return {"success": True, "deactivated": False}


@router.get("/support/tickets")
async def list_support_tickets(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SupportTicket).order_by(SupportTicket.created_at.desc()))
    tickets = result.scalars().all()
    items = []
    for t in tickets:
        user = await db.get(User, t.user_id) if t.user_id else None
        driver = await db.get(Driver, t.driver_id) if t.driver_id else None
        replies = await _ticket_replies(db, t.id)
        items.append(_map_support_ticket(t, user, driver, replies))
    return items


@router.get("/support/tickets/{ticket_id}")
async def get_support_ticket(
    ticket_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket:
        raise NotFoundException("Ticket not found")
    user = await db.get(User, ticket.user_id) if ticket.user_id else None
    driver = await db.get(Driver, ticket.driver_id) if ticket.driver_id else None
    replies = await _ticket_replies(db, ticket.id)
    return _map_support_ticket(ticket, user, driver, replies)


class AdminTicketReplyRequest(BaseModel):
    message: str = Field(..., min_length=1)


@router.post("/support/tickets/{ticket_id}/reply")
async def reply_support_ticket(
    ticket_id: UUID,
    data: AdminTicketReplyRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.notifications.service import NotificationService

    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket:
        raise NotFoundException("Ticket not found")

    reply = SupportTicketReply(
        ticket_id=ticket.id,
        sender_id=admin.id,
        sender_type="ADMIN",
        message=data.message.strip(),
    )
    db.add(reply)
    if ticket.status == SupportTicketStatus.OPEN.value:
        ticket.status = SupportTicketStatus.IN_PROGRESS.value

    notif = NotificationService(db)
    if ticket.user_id:
        await notif.create_in_app(
            title="Support team replied",
            message=f"New reply on: {ticket.subject}",
            notification_type="SYSTEM",
            user_id=ticket.user_id,
            data={"ticket_id": str(ticket.id), "event": "support_reply"},
        )
    elif ticket.driver_id:
        await notif.create_in_app(
            title="Support team replied",
            message=f"New reply on: {ticket.subject}",
            notification_type="SYSTEM",
            driver_id=ticket.driver_id,
            data={"ticket_id": str(ticket.id), "event": "support_reply"},
        )

    await db.flush()
    await db.refresh(ticket)
    user = await db.get(User, ticket.user_id) if ticket.user_id else None
    driver = await db.get(Driver, ticket.driver_id) if ticket.driver_id else None
    replies = await _ticket_replies(db, ticket.id)
    return _map_support_ticket(ticket, user, driver, replies)


@router.patch("/support/tickets/{ticket_id}")
async def update_support_ticket(
    ticket_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket:
        raise NotFoundException("Ticket not found")
    if "status" in data:
        status_map = {
            "open": SupportTicketStatus.OPEN.value,
            "in_progress": SupportTicketStatus.IN_PROGRESS.value,
            "resolved": SupportTicketStatus.RESOLVED.value,
            "closed": SupportTicketStatus.CLOSED.value,
        }
        ticket.status = status_map.get(data["status"], ticket.status)
    if "priority" in data:
        ticket.priority = data["priority"].upper()
    await db.flush()
    await db.refresh(ticket)
    user = await db.get(User, ticket.user_id) if ticket.user_id else None
    driver = await db.get(Driver, ticket.driver_id) if ticket.driver_id else None
    replies = await _ticket_replies(db, ticket.id)
    return _map_support_ticket(ticket, user, driver, replies)


@router.get("/notifications")
async def list_notifications(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Notification).order_by(Notification.created_at.desc()).limit(100))
    items = []
    for n in result.scalars().all():
        items.append({
            "id": str(n.id),
            "title": n.title,
            "message": n.message,
            "target": "all_users",
            "type": "system_alert",
            "channels": ["push"],
            "sentAt": n.created_at.isoformat(),
            "recipientCount": 1,
        })
    return items


@router.get("/alerts")
async def admin_alerts(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    alerts = []
    pending_drivers = (
        await db.execute(
            select(func.count()).select_from(Driver).where(Driver.kyc_status.in_(["PENDING", "SUBMITTED"]), Driver.is_deleted == False)
        )
    ).scalar_one()
    if pending_drivers:
        alerts.append({
            "id": "pending-drivers",
            "title": "Pending Driver Approvals",
            "message": f"{pending_drivers} driver(s) awaiting KYC approval",
            "type": "driver_registration",
            "time": "now",
            "createdAt": _utc_now().isoformat(),
            "unread": True,
            "href": "/drivers",
        })

    open_tickets = (
        await db.execute(
            select(func.count()).select_from(SupportTicket).where(
                SupportTicket.status.in_([SupportTicketStatus.OPEN.value, SupportTicketStatus.IN_PROGRESS.value])
            )
        )
    ).scalar_one()
    if open_tickets:
        alerts.append({
            "id": "open-tickets",
            "title": "Open Support Tickets",
            "message": f"{open_tickets} ticket(s) need attention",
            "type": "support_ticket",
            "time": "now",
            "createdAt": _utc_now().isoformat(),
            "unread": True,
            "href": "/support",
        })

    active_rides = (
        await db.execute(
            select(func.count()).select_from(Ride).where(Ride.status.in_([RideStatus.REQUESTED.value, RideStatus.SEARCHING.value]))
        )
    ).scalar_one()
    if active_rides:
        alerts.append({
            "id": "active-rides",
            "title": "Pending Ride Requests",
            "message": f"{active_rides} ride(s) waiting for drivers",
            "type": "ride_update",
            "time": "now",
            "createdAt": _utc_now().isoformat(),
            "unread": True,
            "href": "/rides",
        })

    return alerts


@router.get("/commission-settings", response_model=VehicleCommissionSettingsResponse)
async def get_commission_settings(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    return await CommissionService(db).get_vehicle_settings_response()


@router.put("/commission-settings", response_model=VehicleCommissionSettingsResponse)
async def update_commission_settings(
    data: VehicleCommissionSettingsUpdate,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    return await CommissionService(db).update_vehicle_settings(data, admin.id)


@router.get("/reports/revenue")
async def revenue_report(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    group_by: str = Query("date", pattern="^(date|driver)$"),
    days: int = Query(30, ge=1, le=365),
):
    since = _utc_now() - timedelta(days=days)

    if group_by == "driver":
        rows = (
            await db.execute(
                select(
                    Ride.driver_id,
                    func.coalesce(func.sum(Ride.final_fare), 0.0).label("total_ride_revenue"),
                    func.coalesce(func.sum(Ride.driver_earning), 0.0).label("total_driver_earnings"),
                    func.coalesce(func.sum(Ride.company_earning), 0.0).label("total_company_earnings"),
                    func.count(Ride.id).label("completed_rides"),
                )
                .where(
                    Ride.status == RideStatus.COMPLETED.value,
                    Ride.completed_at >= since,
                    Ride.driver_id.isnot(None),
                )
                .group_by(Ride.driver_id)
                .order_by(func.sum(Ride.driver_earning).desc())
            )
        ).all()
        return {
            "groupBy": "driver",
            "items": [
                {
                    "driverId": str(row.driver_id),
                    "totalRideRevenue": float(row.total_ride_revenue or 0),
                    "totalDriverEarnings": float(row.total_driver_earnings or 0),
                    "totalCompanyEarnings": float(row.total_company_earnings or 0),
                    "completedRides": int(row.completed_rides or 0),
                }
                for row in rows
            ],
        }

    items = []
    for i in range(days - 1, -1, -1):
        day = (_utc_now() - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day + timedelta(days=1)
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(Ride.final_fare), 0.0),
                    func.coalesce(func.sum(Ride.driver_earning), 0.0),
                    func.coalesce(func.sum(Ride.company_earning), 0.0),
                    func.count(Ride.id),
                ).where(
                    Ride.status == RideStatus.COMPLETED.value,
                    Ride.completed_at >= day,
                    Ride.completed_at < next_day,
                )
            )
        ).one()
        items.append(
            {
                "date": day.date().isoformat(),
                "totalRideRevenue": float(row[0] or 0),
                "totalDriverEarnings": float(row[1] or 0),
                "totalCompanyEarnings": float(row[2] or 0),
                "completedRides": int(row[3] or 0),
            }
        )

    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(Ride.final_fare), 0.0),
                func.coalesce(func.sum(Ride.driver_earning), 0.0),
                func.coalesce(func.sum(Ride.company_earning), 0.0),
                func.count(Ride.id),
            ).where(
                Ride.status == RideStatus.COMPLETED.value,
                Ride.completed_at >= since,
            )
        )
    ).one()

    commission = await CommissionService(db).get_vehicle_settings_response()

    return {
        "groupBy": "date",
        "driverCommissionPercentage": commission.default_commission_percentage,
        "commissionMode": "per_vehicle",
        "totals": {
            "totalRideRevenue": float(totals[0] or 0),
            "totalDriverEarnings": float(totals[1] or 0),
            "totalCompanyEarnings": float(totals[2] or 0),
            "completedRides": int(totals[3] or 0),
        },
        "items": items,
    }


@router.get("/settings")
async def get_settings(admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    return await _get_settings(db)


@router.patch("/settings")
async def update_settings(
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    key_map = {
        "appName": "app_name",
        "contactEmail": "contact_email",
        "contactPhone": "contact_phone",
        "googleMapsApiKey": "google_maps_api_key",
        "firebaseConfig": "firebase_config",
        "razorpayKey": "razorpay_key",
        "stripeKey": "stripe_key",
        "logo": "logo",
    }
    for front_key, db_key in key_map.items():
        if front_key in data:
            result = await db.execute(select(AppSetting).where(AppSetting.key == db_key))
            setting = result.scalar_one_or_none()
            value = str(data[front_key])
            if setting:
                setting.value = value
            else:
                db.add(AppSetting(key=db_key, value=value, is_public=db_key in ("app_name", "contact_email", "contact_phone")))
    await db.flush()
    return await _get_settings(db)


def _map_student_pass_admin(record: StudentPass, user: User | None = None) -> dict:
    payload = map_student_pass(record, mask_aadhar=False)
    if user:
        payload["user"] = {
            "id": str(user.id),
            "name": f"{user.first_name} {user.last_name}".strip(),
            "phone": user.phone,
            "email": user.email,
        }
    return payload


@router.get("/student-passes")
async def list_student_passes(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = select(StudentPass, User).join(User, User.id == StudentPass.user_id)
    if status:
        query = query.where(StudentPass.status == status.upper())
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                User.first_name.ilike(term),
                User.last_name.ilike(term),
                User.phone.ilike(term),
                StudentPass.college_name.ilike(term),
                StudentPass.aadhar_number.ilike(term),
            )
        )
    query = query.order_by(StudentPass.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = [_map_student_pass_admin(row[0], row[1]) for row in result.all()]
    return {"items": items, "page": page, "page_size": page_size}


@router.get("/student-passes/{pass_id}")
async def get_student_pass_detail(
    pass_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(StudentPass, User).join(User, User.id == StudentPass.user_id).where(StudentPass.id == pass_id)
    )
    data = row.first()
    if not data:
        raise NotFoundException("Student pass application not found")
    return _map_student_pass_admin(data[0], data[1])


@router.post("/student-passes/{pass_id}/approve")
async def approve_student_pass(
    pass_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(StudentPass, pass_id)
    if not record:
        raise NotFoundException("Student pass application not found")
    record.status = KYCStatus.APPROVED.value
    record.verified_by_id = admin.id
    record.verified_at = _utc_now()
    record.rejection_reason = None
    await NotificationService(db).create_in_app(
        title="Student Pass Approved",
        message=f"Your student pass is verified. Enjoy {int(record.discount_percent)}% off on every ride.",
        notification_type="SYSTEM",
        user_id=record.user_id,
    )
    await db.flush()
    user = await db.get(User, record.user_id)
    return _map_student_pass_admin(record, user)


class RejectStudentPassRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/student-passes/{pass_id}/reject")
async def reject_student_pass(
    pass_id: UUID,
    data: RejectStudentPassRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(StudentPass, pass_id)
    if not record:
        raise NotFoundException("Student pass application not found")
    record.status = KYCStatus.REJECTED.value
    record.rejection_reason = data.reason.strip()
    record.verified_by_id = admin.id
    record.verified_at = _utc_now()
    await NotificationService(db).create_in_app(
        title="Student Pass Rejected",
        message=data.reason.strip(),
        notification_type="SYSTEM",
        user_id=record.user_id,
    )
    await db.flush()
    user = await db.get(User, record.user_id)
    return _map_student_pass_admin(record, user)


@router.get("/subscription-plans")
async def admin_list_subscription_plans(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc()))
    plans = result.scalars().all()

    count_rows = await db.execute(
        select(UserSubscription.plan_id, func.count(UserSubscription.id))
        .where(UserSubscription.status == "ACTIVE")
        .group_by(UserSubscription.plan_id)
    )
    counts = {str(plan_id): int(count) for plan_id, count in count_rows.all()}

    mapped_plans = [
        map_subscription_plan(plan, subscriber_count=counts.get(str(plan.id), 0)) for plan in plans
    ]
    breakdown = [
        {
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "slug": plan.slug,
            "subscriber_count": counts.get(str(plan.id), 0),
        }
        for plan in plans
    ]
    return {
        "plans": mapped_plans,
        "stats": {
            "total_active_subscribers": sum(counts.values()),
            "plan_breakdown": breakdown,
        },
    }


@router.post("/subscription-plans")
async def admin_create_subscription_plan(
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    import json

    slug = str(data.get("slug", "")).strip().lower()
    if not slug:
        raise ValidationException("Plan slug is required")
    existing = await db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.slug == slug))
    if existing:
        raise ConflictException("Plan slug already exists")
    benefits = data.get("benefits") or []
    plan = SubscriptionPlan(
        slug=slug,
        name=data["name"],
        description=data.get("description"),
        price=float(data.get("price", 0)),
        period_label=data.get("periodLabel", data.get("period_label", "month")),
        benefits_json=json.dumps(benefits),
        ride_discount_percent=float(data.get("rideDiscountPercent", data.get("ride_discount_percent", 0))),
        is_popular=bool(data.get("isPopular", data.get("is_popular", False))),
        is_active=bool(data.get("isActive", data.get("is_active", True))),
        sort_order=int(data.get("sortOrder", data.get("sort_order", 0))),
    )
    db.add(plan)
    await db.flush()
    return map_subscription_plan(plan)


@router.patch("/subscription-plans/{plan_id}")
async def admin_update_subscription_plan(
    plan_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    import json

    plan = await db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise NotFoundException("Subscription plan not found")
    if "name" in data:
        plan.name = data["name"]
    if "description" in data:
        plan.description = data["description"]
    if "price" in data:
        plan.price = float(data["price"])
    if "periodLabel" in data or "period_label" in data:
        plan.period_label = data.get("periodLabel", data.get("period_label"))
    if "benefits" in data:
        plan.benefits_json = json.dumps(data["benefits"])
    if "rideDiscountPercent" in data or "ride_discount_percent" in data:
        plan.ride_discount_percent = float(
            data.get("rideDiscountPercent", data.get("ride_discount_percent", 0))
        )
    if "isPopular" in data or "is_popular" in data:
        plan.is_popular = bool(data.get("isPopular", data.get("is_popular")))
    if "isActive" in data or "is_active" in data:
        plan.is_active = bool(data.get("isActive", data.get("is_active")))
    if "sortOrder" in data or "sort_order" in data:
        plan.sort_order = int(data.get("sortOrder", data.get("sort_order", 0)))
    await db.flush()
    return map_subscription_plan(plan)


@router.delete("/subscription-plans/{plan_id}")
async def admin_delete_subscription_plan(
    plan_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise NotFoundException("Subscription plan not found")
    if plan.slug == "free":
        raise ValidationException("The free plan cannot be deleted")

    active_count = await db.scalar(
        select(func.count(UserSubscription.id)).where(
            UserSubscription.plan_id == plan_id,
            UserSubscription.status == "ACTIVE",
        )
    )
    if active_count and active_count > 0:
        raise ConflictException(
            "This plan has active subscribers. Deactivate it instead of deleting."
        )

    await db.delete(plan)
    await db.flush()
    return {"message": "Subscription plan deleted"}


def _map_subscription_subscriber(sub: UserSubscription, user: User) -> dict:
    plan = sub.plan
    return {
        "id": str(sub.id),
        "user_id": str(user.id),
        "name": f"{user.first_name} {user.last_name}".strip(),
        "phone": user.phone,
        "email": user.email,
        "plan_id": str(plan.id) if plan else None,
        "plan_name": plan.name if plan else "—",
        "plan_slug": plan.slug if plan else "",
        "status": sub.status.lower(),
        "started_at": sub.started_at.isoformat() if sub.started_at else None,
        "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
    }


@router.get("/subscription-subscribers")
async def admin_list_subscription_subscribers(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    plan_id: UUID | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = (
        select(UserSubscription)
        .options(selectinload(UserSubscription.plan))
        .join(User, User.id == UserSubscription.user_id)
        .where(UserSubscription.status == "ACTIVE")
        .order_by(UserSubscription.started_at.desc())
    )

    if plan_id:
        query = query.where(UserSubscription.plan_id == plan_id)

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(User.first_name).like(term),
                func.lower(User.last_name).like(term),
                func.lower(User.phone).like(term),
                func.lower(User.email).like(term),
            )
        )

    count_stmt = (
        select(func.count(UserSubscription.id))
        .join(User, User.id == UserSubscription.user_id)
        .where(UserSubscription.status == "ACTIVE")
    )
    if plan_id:
        count_stmt = count_stmt.where(UserSubscription.plan_id == plan_id)
    if search:
        term = f"%{search.strip().lower()}%"
        count_stmt = count_stmt.where(
            or_(
                func.lower(User.first_name).like(term),
                func.lower(User.last_name).like(term),
                func.lower(User.phone).like(term),
                func.lower(User.email).like(term),
            )
        )
    total = await db.scalar(count_stmt) or 0

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.scalars().all()

    user_ids = [sub.user_id for sub in rows]
    users_result = await db.execute(select(User).where(User.id.in_(user_ids))) if user_ids else None
    users_by_id = {user.id: user for user in users_result.scalars().all()} if users_result else {}

    return {
        "items": [
            _map_subscription_subscriber(sub, users_by_id[sub.user_id])
            for sub in rows
            if sub.user_id in users_by_id
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }
