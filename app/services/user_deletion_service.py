"""Permanently remove a passenger account and free phone/email for fresh signup."""
import secrets
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthDevice, UserSession
from app.core.constants import ACTIVE_RIDE_STATUSES
from app.core.exceptions import ConflictException, NotFoundException
from app.core.security import hash_password
from app.models import Ride, User
from app.payments.models import Payment
from app.users.models import SavedAddress
from app.utils.phone import normalize_phone, phone_lookup_variants


async def _user_has_trip_history(db: AsyncSession, user_id: UUID) -> bool:
    ride = await db.execute(select(Ride.id).where(Ride.user_id == user_id).limit(1))
    if ride.scalar_one_or_none():
        return True
    payment = await db.execute(select(Payment.id).where(Payment.user_id == user_id).limit(1))
    return payment.scalar_one_or_none() is not None


async def _anonymize_user(db: AsyncSession, user: User) -> None:
    """Keep ride/payment history but release phone and email for a new account."""
    token = secrets.token_hex(8)
    user.email = f"deleted_{user.id.hex}@deleted.ridebook.app"
    user.phone = f"+del{user.id.hex[:14]}"
    user.password_hash = hash_password(token)
    user.first_name = "Deleted"
    user.last_name = "User"
    user.profile_photo = None
    user.emergency_contact_name = None
    user.emergency_contact_phone = None
    user.fcm_token = None
    user.google_id = None
    user.referral_code = None
    user.referred_by_id = None
    user.otp = None
    user.otp_expires_at = None
    user.is_active = False
    user.is_verified = False
    user.token_version += 1
    user.soft_delete()

    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.execute(delete(AuthDevice).where(AuthDevice.user_id == user.id))
    await db.execute(delete(SavedAddress).where(SavedAddress.user_id == user.id))
    await db.flush()


async def permanently_delete_user(db: AsyncSession, user_id: UUID) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("User not found")

    active_ride = await db.execute(
        select(Ride.id)
        .where(Ride.user_id == user_id, Ride.status.in_(tuple(ACTIVE_RIDE_STATUSES)))
        .limit(1)
    )
    if active_ride.scalar_one_or_none():
        raise ConflictException("Cannot delete user while they have an active ride")

    if await _user_has_trip_history(db, user_id):
        await _anonymize_user(db, user)
        return

    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await db.execute(delete(AuthDevice).where(AuthDevice.user_id == user_id))
    await db.delete(user)
    await db.flush()


def _user_signup_conflict_conditions(phone: str, email: str):
    normalized = normalize_phone(phone)
    variants = phone_lookup_variants(normalized)
    digits = "".join(c for c in normalized if c.isdigit())
    local = digits[-10:] if len(digits) >= 10 else None

    conditions = [User.email == email, *[User.phone == variant for variant in variants]]
    email_local = email.split("@", 1)[0]
    if email_local:
        conditions.append(User.email.like(f"{email_local}@%"))
    if local:
        conditions.append(User.phone.like(f"%{local}"))
    return or_(*conditions)


async def purge_soft_deleted_user_signup_conflicts(
    db: AsyncSession,
    phone: str,
    email: str,
) -> None:
    """Remove old soft-deleted users that still block phone/email reuse."""
    conflict_filter = _user_signup_conflict_conditions(phone, email)
    result = await db.execute(
        select(User).where(and_(User.is_deleted == True, conflict_filter))
    )
    stale_users = result.scalars().all()
    for user in stale_users:
        if await _user_has_trip_history(db, user.id):
            await _anonymize_user(db, user)
        else:
            await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
            await db.execute(delete(AuthDevice).where(AuthDevice.user_id == user.id))
            await db.delete(user)
    if stale_users:
        await db.flush()