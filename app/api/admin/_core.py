"""Admin core routes — users, drivers, auth."""
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_admin
from app.auth.service import AuthService
from app.core.constants import DriverStatus, KYCStatus, RideStatus
from app.core.exceptions import NotFoundException
from app.database.session import get_db
from app.commission.models import DriverWallet
from app.models import AdminUser, Driver, DriverDocument, Ride, User, Vehicle, Wallet, WalletTransaction, Notification, SupportTicket
from app.drivers.models import DriverBankAccount
from app.schemas.admin import AdminLogin
from app.schemas.user import RefreshTokenRequest
from app.schemas.common import TokenResponse
from app.services.driver_deletion_service import permanently_delete_driver
from app.services.user_deletion_service import permanently_delete_user
from app.services.user_benefits_service import map_student_pass, map_subscription_plan
from app.notifications.service import NotificationService
from app.subscriptions.models import StudentPass, UserSubscription
from app.utils.phone import format_phone_display

router = APIRouter(tags=["Admin"])


class AdminLoginResponse(BaseModel):
    user: dict
    accessToken: str
    refreshToken: str
    expiresAt: int


def _user_status(user: User) -> str:
    if not user.is_active:
        return "blocked"
    if not user.is_verified:
        return "inactive"
    return "active"


def _map_user(user: User, wallet_balance: float = 0.0, total_rides: int = 0) -> dict:
    email = user.email or ""
    # Hide auto-generated placeholder emails (phone@ridebook.app) in admin.
    # Admin should show only user-provided details; missing values stay blank.
    if email.endswith("@ridebook.app"):
        local = email.split("@", 1)[0]
        digits = "".join(ch for ch in (user.phone or "") if ch.isdigit())
        if local and digits and local in digits:
            email = ""
    return {
        "id": str(user.id),
        "name": f"{user.first_name} {user.last_name}".strip(),
        "mobile": user.phone,
        "email": email,
        "registrationDate": user.created_at.date().isoformat(),
        "totalRides": total_rides,
        "walletBalance": wallet_balance,
        "status": _user_status(user),
        "avatar": user.profile_photo,
        "city": "",
        "rating": round(float(getattr(user, "rating_avg", 0.0) or 0.0), 2),
        "emergencyContactName": user.emergency_contact_name or "",
        "emergencyContactPhone": format_phone_display(user.emergency_contact_phone)
        if user.emergency_contact_phone
        else "",
    }


def _driver_status(driver: Driver) -> str:
    if driver.kyc_status == KYCStatus.REJECTED.value:
        return "rejected"
    if driver.kyc_status in (KYCStatus.PENDING.value, KYCStatus.SUBMITTED.value):
        return "pending"
    if not driver.is_active:
        return "suspended"
    status_map = {
        DriverStatus.ONLINE.value: "online",
        DriverStatus.OFFLINE.value: "offline",
        DriverStatus.ON_RIDE.value: "busy",
        DriverStatus.BUSY.value: "busy",
    }
    return status_map.get(driver.status, "offline")


def _map_driver(
    driver: Driver,
    vehicle: Vehicle | None = None,
    wallet_balance: float = 0.0,
    bank: DriverBankAccount | None = None,
    commission_earnings: float = 0.0,
) -> dict:
    vehicle_type = "sedan"
    vehicle_number = ""
    vehicle_brand = ""
    vehicle_model = ""
    vehicle_color = ""
    vehicle_year: int | None = None
    vehicle_status = ""
    if vehicle:
        vehicle_number = vehicle.license_plate
        vehicle_brand = vehicle.make
        vehicle_model = vehicle.model
        vehicle_color = vehicle.color
        vehicle_year = vehicle.year
        vehicle_status = vehicle.status
        if vehicle.vehicle_type:
            vehicle_type = vehicle.vehicle_type.name.lower().replace(" ", "_")

    payload = {
        "id": str(driver.id),
        "name": f"{driver.first_name} {driver.last_name}".strip(),
        "phone": driver.phone,
        "email": driver.email,
        "vehicleType": vehicle_type,
        "vehicleNumber": vehicle_number,
        "vehicleBrand": vehicle_brand,
        "vehicleModel": vehicle_model,
        "vehicleColor": vehicle_color,
        "vehicleYear": vehicle_year,
        "vehicleStatus": vehicle_status,
        "rating": driver.rating_avg,
        "totalTrips": driver.total_rides,
        "earnings": commission_earnings,
        "walletBalance": wallet_balance,
        "status": _driver_status(driver),
        "avatar": driver.profile_photo,
        "city": driver.city or "",
        "state": driver.state or "",
        "country": driver.country or "",
        "pinCode": driver.pin_code or "",
        "address": driver.address_line or "",
        "licenseNumber": driver.license_number or "",
        "dateOfBirth": driver.date_of_birth.isoformat() if driver.date_of_birth else None,
        "gender": driver.gender or "",
        "kycStatus": driver.kyc_status,
        "isVerified": driver.is_verified,
        "referralCode": driver.referral_code or "",
        "joinedDate": driver.created_at.date().isoformat(),
    }

    if bank:
        payload["bankDetails"] = {
            "accountHolder": bank.account_holder_name,
            "accountNumber": bank.account_number_masked,
            "ifsc": bank.ifsc_code,
            "bankName": bank.bank_name,
            "upiId": bank.upi_id or "",
            "isVerified": bank.is_verified,
        }

    return payload


async def _driver_wallet_balance(db: AsyncSession, driver_id: UUID) -> float:
    result = await db.execute(
        select(DriverWallet.available_balance).where(DriverWallet.driver_id == driver_id)
    )
    balance = result.scalar_one_or_none()
    if balance is not None:
        return float(balance)

    legacy = await db.execute(
        select(Wallet.balance).where(Wallet.driver_id == driver_id, Wallet.is_active == True)
    )
    return float(legacy.scalar_one_or_none() or 0.0)


async def _driver_commission_earnings(db: AsyncSession, driver_id: UUID) -> float:
    result = await db.execute(
        select(func.coalesce(func.sum(Ride.driver_earning), 0.0)).where(
            Ride.driver_id == driver_id,
            Ride.status == RideStatus.COMPLETED.value,
            Ride.driver_earning.isnot(None),
        )
    )
    return float(result.scalar_one() or 0.0)


def _map_ride(ride: Ride, user: User | None = None, driver: Driver | None = None) -> dict:
    status_map = {
        RideStatus.REQUESTED.value: "requested",
        RideStatus.SEARCHING.value: "requested",
        RideStatus.ACCEPTED.value: "driver_assigned",
        RideStatus.DRIVER_ARRIVED.value: "driver_arrived",
        RideStatus.STARTED.value: "ride_started",
        RideStatus.COMPLETED.value: "ride_completed",
        RideStatus.CANCELLED.value: "cancelled",
    }
    return {
        "id": str(ride.id),
        "userId": str(ride.user_id),
        "userName": f"{user.first_name} {user.last_name}" if user else "",
        "driverId": str(ride.driver_id) if ride.driver_id else None,
        "driverName": f"{driver.first_name} {driver.last_name}" if driver else None,
        "vehicleType": "sedan",
        "pickupLocation": ride.pickup_address,
        "dropLocation": ride.dropoff_address,
        "distance": ride.actual_distance_km or ride.estimated_distance_km,
        "fare": ride.final_fare or ride.estimated_fare,
        "driverCommissionPercentage": ride.driver_commission_percentage,
        "driverEarning": ride.driver_earning,
        "companyEarning": ride.company_earning,
        "status": status_map.get(ride.status, ride.status.lower()),
        "date": ride.created_at.isoformat(),
        "duration": int(ride.actual_duration_min or ride.estimated_duration_min),
        "paymentMethod": ride.payment_method.lower(),
    }


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(data: AdminLogin, db: AsyncSession = Depends(get_db)):
    tokens = await AuthService(db).login_admin(data.email, data.password)
    result = await db.execute(select(AdminUser).where(AdminUser.email == data.email))
    admin = result.scalar_one_or_none()
    return AdminLoginResponse(
        user={
            "email": admin.email if admin else data.email,
            "name": f"{admin.first_name} {admin.last_name}" if admin else "Admin",
            "role": "Super Admin",
            "phone": "+91 00000 00000",
        },
        accessToken=tokens.access_token,
        refreshToken=tokens.refresh_token,
        expiresAt=int(datetime.now(timezone.utc).timestamp()) + 86400,
    )


@router.get("/me")
async def admin_me(admin: Annotated[AdminUser, Depends(get_current_admin)]):
    return {
        "email": admin.email,
        "name": f"{admin.first_name} {admin.last_name}",
        "role": "Super Admin",
        "phone": "+91 00000 00000",
    }


@router.post("/logout")
async def admin_logout(admin: Annotated[AdminUser, Depends(get_current_admin)]):
    return {"message": "Logged out"}


@router.post("/refresh-token", response_model=TokenResponse)
async def admin_refresh(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    return await AuthService(db).refresh_token(data.refresh_token)


@router.get("/users")
async def list_users(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(User).where(User.is_deleted == False)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(User.first_name.ilike(term), User.last_name.ilike(term), User.email.ilike(term), User.phone.ilike(term))
        )
    if status and status != "all":
        if status == "active":
            query = query.where(User.is_active == True, User.is_verified == True)
        elif status == "blocked":
            query = query.where(User.is_active == False)
        elif status == "inactive":
            query = query.where(User.is_verified == False)

    total = (await db.execute(select(func.count()).select_from(User).where(User.is_deleted == False))).scalar_one()
    result = await db.execute(query.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit))
    users = result.scalars().all()

    items = []
    for u in users:
        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == u.id, Wallet.is_active == True).limit(1)
        )
        wallet = wallet_result.scalars().first()
        rides_count = (
            await db.execute(select(func.count()).select_from(Ride).where(Ride.user_id == u.id))
        ).scalar_one()
        items.append(_map_user(u, wallet.balance if wallet else 0.0, rides_count))

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == False))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("User not found")
    wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user.id))
    wallet = wallet_result.scalar_one_or_none()
    rides_count = (await db.execute(select(func.count()).select_from(Ride).where(Ride.user_id == user.id))).scalar_one()
    return _map_user(user, wallet.balance if wallet else 0.0, rides_count)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("User not found")
    if data.get("name"):
        parts = str(data["name"]).split(" ", 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
    if data.get("email"):
        user.email = data["email"]
    if data.get("mobile"):
        user.phone = data["mobile"]
    await db.flush()
    return _map_user(user)


async def _set_user_active(user_id: UUID, db: AsyncSession, active: bool, verified: bool | None = None) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("User not found")
    user.is_active = active
    if verified is not None:
        user.is_verified = verified
    await db.flush()
    return _map_user(user)


@router.post("/users/{user_id}/suspend")
async def suspend_user(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    return await _set_user_active(user_id, db, active=False)


@router.post("/users/{user_id}/block")
async def block_user(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    return await _set_user_active(user_id, db, active=False)


@router.post("/users/{user_id}/activate")
async def activate_user(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    return await _set_user_active(user_id, db, active=True, verified=True)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    await permanently_delete_user(db, user_id)
    return {"message": "User permanently deleted", "id": str(user_id)}


@router.post("/users/{user_id}/reset")
async def reset_user(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    return await get_user(user_id, admin, db)


@router.get("/users/{user_id}/rides")
async def user_rides(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ride).where(Ride.user_id == user_id).order_by(Ride.created_at.desc()).limit(50)
    )
    rides = result.scalars().all()
    return [_map_ride(r) for r in rides]


@router.get("/users/{user_id}/wallet")
async def user_wallet(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = wallet_result.scalar_one_or_none()
    if not wallet:
        return {"balance": 0.0, "transactions": []}

    tx_result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
    )
    transactions = []
    for tx in tx_result.scalars().all():
        ride_id = tx.reference_id if (tx.reference_type or "").lower() in ("ride", "rides") else None
        is_debit = (tx.transaction_type or "").upper() == "DEBIT"
        signed_amount = -tx.amount if is_debit else tx.amount
        transactions.append(
            {
                "id": str(tx.id),
                "userId": str(user_id),
                "description": tx.description,
                "amount": signed_amount,
                "type": (tx.transaction_type or "").lower(),
                "status": "completed",
                "date": tx.created_at.isoformat(),
                "rideId": ride_id,
            }
        )
    return {"balance": wallet.balance, "transactions": transactions}


@router.get("/users/{user_id}/support-tickets")
async def user_tickets(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.created_at.desc())
        .limit(100)
    )
    tickets = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "userId": str(user_id),
            "subject": t.subject,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "createdAt": t.created_at.isoformat(),
            "updatedAt": t.updated_at.isoformat(),
        }
        for t in tickets
    ]


@router.get("/users/{user_id}/activity-logs")
async def user_logs(user_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    )
    logs = result.scalars().all()
    return [
        {
            "id": str(n.id),
            "userId": str(user_id),
            "action": f"{n.title}: {n.message}" if n.message else n.title,
            "timestamp": n.created_at.isoformat(),
        }
        for n in logs
    ]


@router.get("/users/{user_id}/subscription")
async def user_subscription(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id, User.is_deleted == False))
    if not user:
        raise NotFoundException("User not found")

    sub = await db.scalar(
        select(UserSubscription)
        .options(selectinload(UserSubscription.plan))
        .where(UserSubscription.user_id == user_id, UserSubscription.status == "ACTIVE")
    )
    if not sub or not sub.plan:
        return {"subscription": None}

    return {
        "subscription": {
            "plan": map_subscription_plan(sub.plan),
            "status": sub.status.lower(),
            "started_at": sub.started_at.isoformat() if sub.started_at else None,
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        }
    }


@router.get("/users/{user_id}/student-pass")
async def user_student_pass(
    user_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id, User.is_deleted == False))
    if not user:
        raise NotFoundException("User not found")

    record = await db.scalar(select(StudentPass).where(StudentPass.user_id == user_id))
    if not record:
        return {"application": None}

    return {"application": map_student_pass(record, mask_aadhar=False)}


@router.get("/drivers")
async def list_drivers(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(Driver).where(Driver.is_deleted == False)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(Driver.first_name.ilike(term), Driver.last_name.ilike(term), Driver.email.ilike(term), Driver.phone.ilike(term))
        )

    total = (await db.execute(select(func.count()).select_from(Driver).where(Driver.is_deleted == False))).scalar_one()
    result = await db.execute(query.order_by(Driver.created_at.desc()).offset((page - 1) * limit).limit(limit))
    drivers = result.scalars().all()

    items = []
    for d in drivers:
        vehicle_result = await db.execute(
            select(Vehicle)
            .options(selectinload(Vehicle.vehicle_type))
            .where(Vehicle.driver_id == d.id, Vehicle.is_deleted == False)
            .limit(1)
        )
        vehicle = vehicle_result.scalar_one_or_none()
        wallet_balance = await _driver_wallet_balance(db, d.id)
        commission_earnings = await _driver_commission_earnings(db, d.id)
        mapped = _map_driver(d, vehicle, wallet_balance, commission_earnings=commission_earnings)
        if status and status != "all" and mapped["status"] != status:
            continue
        items.append(mapped)

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/drivers/{driver_id}")
async def get_driver(
    driver_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Driver).where(Driver.id == driver_id, Driver.is_deleted == False))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    vehicle_result = await db.execute(
        select(Vehicle)
        .options(selectinload(Vehicle.vehicle_type))
        .where(Vehicle.driver_id == driver.id, Vehicle.is_deleted == False)
        .limit(1)
    )
    vehicle = vehicle_result.scalar_one_or_none()
    wallet_balance = await _driver_wallet_balance(db, driver.id)
    commission_earnings = await _driver_commission_earnings(db, driver.id)
    bank_result = await db.execute(
        select(DriverBankAccount)
        .where(DriverBankAccount.driver_id == driver.id)
        .order_by(DriverBankAccount.is_primary.desc())
        .limit(1)
    )
    bank = bank_result.scalar_one_or_none()
    return _map_driver(driver, vehicle, wallet_balance, bank, commission_earnings)


@router.patch("/drivers/{driver_id}")
async def update_driver(
    driver_id: UUID,
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    if data.get("name"):
        parts = str(data["name"]).split(" ", 1)
        driver.first_name = parts[0]
        driver.last_name = parts[1] if len(parts) > 1 else ""
    if data.get("email"):
        driver.email = data["email"]
    if data.get("phone"):
        driver.phone = data["phone"]
    if data.get("status"):
        status_map = {"online": DriverStatus.ONLINE.value, "offline": DriverStatus.OFFLINE.value, "busy": DriverStatus.ON_RIDE.value}
        if data["status"] in status_map:
            driver.status = status_map[data["status"]]
    await db.flush()
    return _map_driver(driver)


@router.post("/drivers/{driver_id}/approve")
async def approve_driver(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    driver.kyc_status = KYCStatus.APPROVED.value
    driver.is_verified = True
    await db.execute(
        update(DriverDocument)
        .where(DriverDocument.driver_id == driver.id)
        .values(status=KYCStatus.APPROVED.value)
    )
    await NotificationService(db).create_in_app(
        title="KYC Approved",
        message="Your documents are verified. You can go online and accept rides.",
        notification_type="SYSTEM",
        driver_id=driver.id,
    )
    await db.flush()
    return _map_driver(driver)


@router.post("/drivers/{driver_id}/reject")
async def reject_driver(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    driver.kyc_status = KYCStatus.REJECTED.value
    await NotificationService(db).create_in_app(
        title="Documents Rejected",
        message="Please update your documents and resubmit for verification.",
        notification_type="SYSTEM",
        driver_id=driver.id,
    )
    await db.flush()
    return _map_driver(driver)


@router.post("/drivers/{driver_id}/suspend")
async def suspend_driver(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    driver.is_active = False
    await db.flush()
    return _map_driver(driver)


@router.post("/drivers/{driver_id}/reactivate")
async def reactivate_driver(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")
    driver.is_active = True
    await db.flush()
    return _map_driver(driver)


@router.delete("/drivers/{driver_id}")
async def delete_driver(
    driver_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    await permanently_delete_driver(db, driver_id)
    return {"message": "Driver permanently deleted", "id": str(driver_id)}


@router.get("/drivers/{driver_id}/rides")
async def driver_rides(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Ride).where(Ride.driver_id == driver_id).order_by(Ride.created_at.desc()).limit(50))
    return [_map_ride(r) for r in result.scalars().all()]


@router.get("/drivers/{driver_id}/documents")
async def driver_documents(driver_id: UUID, admin: Annotated[AdminUser, Depends(get_current_admin)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriverDocument).where(DriverDocument.driver_id == driver_id))
    docs = result.scalars().all()
    return {
        "documents": [
            {
                "id": str(d.id),
                "driverId": str(d.driver_id),
                "type": d.document_type.lower(),
                "name": d.document_type.replace("_", " ").title(),
                "status": d.status.lower(),
                "uploadedAt": d.created_at.isoformat(),
                "url": d.document_url,
            }
            for d in docs
        ]
    }
