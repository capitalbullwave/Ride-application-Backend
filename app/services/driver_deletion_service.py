"""Permanently remove a driver account and related data."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthDevice, UserSession
from app.commission.models import DriverWallet, DriverWalletTransaction
from app.core.constants import DRIVER_ACTIVE_RIDE_STATUSES, RideStatus
from app.core.exceptions import NotFoundException
from app.drivers.models import (
    DriverBankAccount,
    DriverDocument,
    DriverEmergencyContact,
    DriverLocation,
)
from app.models import Driver, Ride
from app.notifications.models import Notification
from app.ratings.models import Rating
from app.services.driver_matching import DRIVER_PENDING_PREFIX, DriverMatchingService
from app.services.image_storage import delete_driver_uploads
from app.support.models import SupportTicket
from app.utils.phone import normalize_phone, phone_lookup_variants
from app.vehicles.models import Vehicle
from app.wallet.models import Wallet, WalletTransaction, WithdrawalRequest


def _signup_conflict_conditions(phone: str, email: str):
    normalized = normalize_phone(phone)
    variants = phone_lookup_variants(normalized)
    digits = "".join(c for c in normalized if c.isdigit())
    local = digits[-10:] if len(digits) >= 10 else None

    conditions = [Driver.email == email, *[Driver.phone == variant for variant in variants]]
    email_local = email.split("@", 1)[0]
    if email_local:
        conditions.append(Driver.email.like(f"{email_local}@%"))
    if local:
        conditions.append(Driver.phone.like(f"%{local}"))
    return or_(*conditions)


async def _unlink_driver_rides(db: AsyncSession, driver_id: UUID) -> None:
    """Cancel active trips and detach the driver from ride history."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(Ride)
        .where(
            Ride.driver_id == driver_id,
            Ride.status.in_(tuple(DRIVER_ACTIVE_RIDE_STATUSES)),
        )
        .values(
            status=RideStatus.CANCELLED.value,
            cancelled_at=now,
            cancelled_by="ADMIN",
            cancellation_reason="Driver account deleted by admin",
        )
    )
    await db.execute(
        update(Ride).where(Ride.driver_id == driver_id).values(driver_id=None, vehicle_id=None)
    )
    await db.execute(
        update(Ride)
        .where(
            Ride.vehicle_id.in_(select(Vehicle.id).where(Vehicle.driver_id == driver_id))
        )
        .values(vehicle_id=None)
    )


async def _delete_driver_dependencies(db: AsyncSession, driver_id: UUID) -> None:
    """Delete child rows explicitly — SQLAlchemy ORM delete can nullify required FKs."""
    await db.execute(delete(WithdrawalRequest).where(WithdrawalRequest.driver_id == driver_id))
    await db.execute(
        delete(WalletTransaction).where(
            WalletTransaction.wallet_id.in_(
                select(Wallet.id).where(Wallet.driver_id == driver_id)
            )
        )
    )
    await db.execute(delete(Wallet).where(Wallet.driver_id == driver_id))
    await db.execute(
        delete(DriverWalletTransaction).where(DriverWalletTransaction.driver_id == driver_id)
    )
    await db.execute(delete(DriverWallet).where(DriverWallet.driver_id == driver_id))
    await db.execute(delete(DriverBankAccount).where(DriverBankAccount.driver_id == driver_id))
    await db.execute(delete(DriverDocument).where(DriverDocument.driver_id == driver_id))
    await db.execute(delete(DriverLocation).where(DriverLocation.driver_id == driver_id))
    await db.execute(
        delete(DriverEmergencyContact).where(DriverEmergencyContact.driver_id == driver_id)
    )
    await _unlink_driver_rides(db, driver_id)
    await db.execute(delete(Vehicle).where(Vehicle.driver_id == driver_id))
    await db.execute(delete(Rating).where(Rating.driver_id == driver_id))
    await db.execute(delete(Notification).where(Notification.driver_id == driver_id))
    await db.execute(delete(UserSession).where(UserSession.driver_id == driver_id))
    await db.execute(delete(AuthDevice).where(AuthDevice.driver_id == driver_id))
    await db.execute(
        update(SupportTicket).where(SupportTicket.driver_id == driver_id).values(driver_id=None)
    )


async def purge_soft_deleted_driver_signup_conflicts(
    db: AsyncSession,
    phone: str,
    email: str,
) -> None:
    """Remove old soft-deleted drivers that still block phone/email reuse."""
    conflict_filter = _signup_conflict_conditions(phone, email)
    result = await db.execute(
        select(Driver).where(and_(Driver.is_deleted == True, conflict_filter))
    )
    stale_drivers = result.scalars().all()
    for driver in stale_drivers:
        await _delete_driver_dependencies(db, driver.id)
        delete_driver_uploads(str(driver.id))
        await db.execute(delete(Driver).where(Driver.id == driver.id))
    if stale_drivers:
        await db.flush()


async def permanently_delete_driver(db: AsyncSession, driver_id: UUID) -> None:
    result = await db.execute(select(Driver).where(Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise NotFoundException("Driver not found")

    matching = DriverMatchingService(db)
    await matching.set_driver_offline(driver_id)
    redis = await matching._get_redis()
    if redis:
        try:
            await redis.delete(f"{DRIVER_PENDING_PREFIX}{driver_id}")
        except Exception:
            pass

    await _delete_driver_dependencies(db, driver_id)
    delete_driver_uploads(str(driver_id))
    await db.execute(delete(Driver).where(Driver.id == driver_id))
    await db.flush()
