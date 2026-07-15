"""Refer & Earn — admin-configured programs, attributions, and payouts."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RideStatus
from app.core.exceptions import NotFoundException, ValidationException
from app.coupons.models import ReferralProgram, ReferralReward
from app.drivers.models import Driver
from app.models import Ride, User
from app.services.driver_wallet_service import DriverWalletService
from app.services.payment_service import WalletService

AUDIENCE_USER = "USER"
AUDIENCE_DRIVER = "DRIVER"
STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_CANCELLED = "CANCELLED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Skip 0/O/1/I so codes are easier to read and less collision-prone visually.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _random_code(prefix: str, length: int = 8) -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    return f"{prefix}{body}"


def _format_share_message(
    template: Optional[str],
    *,
    code: str,
    reward_amount: float,
    required_rides: int,
    audience: str,
) -> str:
    default = (
        "Join Bull Wave Rides with my code {code}. Complete {rides} rides and I earn ₹{reward}!"
        if audience == AUDIENCE_USER
        else "Join Bull Wave Rides as a captain with my code {code}. Complete {rides} trips and I earn ₹{reward}!"
    )
    text = (template or default).strip() or default
    return (
        text.replace("{code}", code)
        .replace("{reward}", str(int(reward_amount) if float(reward_amount).is_integer() else reward_amount))
        .replace("{rides}", str(int(required_rides)))
    )


class ReferralService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_program(self, audience: str) -> Optional[ReferralProgram]:
        result = await self.db.execute(
            select(ReferralProgram).where(ReferralProgram.audience == audience.upper())
        )
        return result.scalar_one_or_none()

    async def ensure_default_programs(self) -> list[ReferralProgram]:
        programs: list[ReferralProgram] = []
        for audience, title, description in (
            (
                AUDIENCE_USER,
                "Refer & Earn",
                "Share your code with friends. When they complete the required rides, you earn a reward.",
            ),
            (
                AUDIENCE_DRIVER,
                "Refer & Earn",
                "Invite drivers with your code. When they complete the required trips, you earn a reward.",
            ),
        ):
            program = await self.get_program(audience)
            if not program:
                program = ReferralProgram(
                    audience=audience,
                    is_enabled=True,
                    required_rides=5,
                    reward_amount=100.0,
                    title=title,
                    description=description,
                    terms="Reward is credited to your wallet after the referred person completes the required rides.",
                    share_message=(
                        "Join Bull Wave Rides with my code {code}. "
                        "Complete {rides} rides and I earn ₹{reward}!"
                        if audience == AUDIENCE_USER
                        else "Join Bull Wave Rides as a captain with my code {code}. "
                        "Complete {rides} trips and I earn ₹{reward}!"
                    ),
                )
                self.db.add(program)
                await self.db.flush()
            programs.append(program)
        return programs

    async def upsert_program(self, audience: str, data: dict) -> ReferralProgram:
        audience = audience.upper()
        if audience not in {AUDIENCE_USER, AUDIENCE_DRIVER}:
            raise ValidationException("audience must be USER or DRIVER")

        program = await self.get_program(audience)
        if not program:
            program = ReferralProgram(audience=audience)
            self.db.add(program)

        if "is_enabled" in data:
            program.is_enabled = bool(data["is_enabled"])
        if "required_rides" in data:
            rides = int(data["required_rides"])
            if rides < 1:
                raise ValidationException("required_rides must be at least 1")
            program.required_rides = rides
        if "reward_amount" in data:
            amount = float(data["reward_amount"])
            if amount < 0:
                raise ValidationException("reward_amount cannot be negative")
            program.reward_amount = amount
        if "title" in data and data["title"]:
            program.title = str(data["title"]).strip()[:120]
        if "description" in data:
            program.description = (str(data["description"]).strip() or None)
        if "terms" in data:
            program.terms = (str(data["terms"]).strip() or None)
        if "share_message" in data:
            program.share_message = (str(data["share_message"]).strip() or None)

        await self.db.flush()
        await self.db.refresh(program)
        return program

    @staticmethod
    def serialize_program(program: ReferralProgram) -> dict:
        return {
            "id": str(program.id),
            "audience": program.audience,
            "isEnabled": program.is_enabled,
            "requiredRides": program.required_rides,
            "rewardAmount": program.reward_amount,
            "title": program.title,
            "description": program.description,
            "terms": program.terms,
            "shareMessage": program.share_message,
            "updatedAt": program.updated_at.isoformat() if program.updated_at else None,
        }

    async def _code_taken(self, code: str) -> bool:
        """Ensure invite codes stay unique across users and drivers."""
        user_hit = await self.db.scalar(select(User.id).where(User.referral_code == code))
        if user_hit:
            return True
        driver_hit = await self.db.scalar(select(Driver.id).where(Driver.invite_code == code))
        return driver_hit is not None

    async def ensure_user_invite_code(self, user: User) -> str:
        if user.referral_code:
            return user.referral_code
        for _ in range(12):
            code = _random_code("U", 8)
            if not await self._code_taken(code):
                user.referral_code = code
                await self.db.flush()
                return code
        raise ValidationException("Unable to generate referral code")

    async def ensure_driver_invite_code(self, driver: Driver) -> str:
        if driver.invite_code:
            return driver.invite_code
        for _ in range(12):
            code = _random_code("D", 8)
            if not await self._code_taken(code):
                driver.invite_code = code
                await self.db.flush()
                return code
        raise ValidationException("Unable to generate referral code")

    async def _existing_reward_for_referee(
        self, audience: str, referee_id: UUID
    ) -> Optional[ReferralReward]:
        return await self.db.scalar(
            select(ReferralReward).where(
                ReferralReward.audience == audience,
                ReferralReward.referee_id == referee_id,
            )
        )

    async def _create_reward(
        self,
        *,
        audience: str,
        program: ReferralProgram,
        referrer_id: UUID,
        referee_id: UUID,
    ) -> ReferralReward:
        existing = await self._existing_reward_for_referee(audience, referee_id)
        if existing:
            raise ValidationException(
                "Referral code already applied. Only one referral is allowed per account."
            )

        reward = ReferralReward(
            audience=audience,
            program_id=program.id,
            referrer_id=referrer_id,
            referee_id=referee_id,
            required_rides=program.required_rides,
            reward_amount=program.reward_amount,
            rides_completed=0,
            status=STATUS_PENDING,
        )
        self.db.add(reward)
        await self.db.flush()
        return reward

    async def apply_user_referral(self, user: User, code: str) -> ReferralReward:
        """User app: only USER invite codes. One referral per user account."""
        code = (code or "").strip().upper()
        if not code:
            raise ValidationException("Referral code is required")

        await self.ensure_default_programs()

        existing = await self._existing_reward_for_referee(AUDIENCE_USER, user.id)
        if user.referred_by_id or existing:
            raise ValidationException(
                "Referral code already applied. Only one referral is allowed per account."
            )

        program = await self.get_program(AUDIENCE_USER)
        if not program or not program.is_enabled:
            raise ValidationException("Refer & Earn is not active right now")

        # Driver codes must never apply on user accounts
        driver_code = await self.db.scalar(select(Driver.id).where(Driver.invite_code == code))
        if driver_code:
            raise ValidationException(
                "This is a driver invite code. Only user referral codes work in the user app."
            )

        referrer = await self.db.scalar(select(User).where(User.referral_code == code))
        if not referrer:
            raise NotFoundException("Invalid referral code")
        if referrer.id == user.id:
            raise ValidationException("You cannot use your own referral code")

        user.referred_by_id = referrer.id
        return await self._create_reward(
            audience=AUDIENCE_USER,
            program=program,
            referrer_id=referrer.id,
            referee_id=user.id,
        )

    async def apply_driver_referral(self, driver: Driver, code: Optional[str]) -> Optional[ReferralReward]:
        """Driver app: only DRIVER invite codes. One referral per driver account."""
        code = (code or "").strip().upper()
        if not code:
            return None

        await self.ensure_default_programs()

        existing = await self._existing_reward_for_referee(AUDIENCE_DRIVER, driver.id)
        if existing:
            raise ValidationException(
                "Referral code already applied. Only one referral is allowed per account."
            )

        program = await self.get_program(AUDIENCE_DRIVER)
        if not program or not program.is_enabled:
            raise ValidationException("Refer & Earn is not active right now")

        # User codes must never apply on driver accounts
        user_code = await self.db.scalar(select(User.id).where(User.referral_code == code))
        if user_code:
            raise ValidationException(
                "This is a user referral code. Only driver invite codes work in the driver app."
            )

        referrer = await self.db.scalar(select(Driver).where(Driver.invite_code == code))
        if not referrer:
            raise NotFoundException("Invalid referral code")
        if referrer.id == driver.id:
            raise ValidationException("You cannot use your own referral code")

        # Store the code this captain joined with (distinct from invite_code they share)
        driver.referral_code = code
        return await self._create_reward(
            audience=AUDIENCE_DRIVER,
            program=program,
            referrer_id=referrer.id,
            referee_id=driver.id,
        )

    async def _count_completed_rides_for_user(self, user_id: UUID) -> int:
        result = await self.db.scalar(
            select(func.count())
            .select_from(Ride)
            .where(Ride.user_id == user_id, Ride.status == RideStatus.COMPLETED.value)
        )
        return int(result or 0)

    async def _count_completed_rides_for_driver(self, driver_id: UUID) -> int:
        result = await self.db.scalar(
            select(func.count())
            .select_from(Ride)
            .where(Ride.driver_id == driver_id, Ride.status == RideStatus.COMPLETED.value)
        )
        return int(result or 0)

    async def _pay_reward(self, reward: ReferralReward) -> None:
        if reward.status == STATUS_PAID:
            return
        if reward.status == STATUS_CANCELLED:
            return
        if reward.audience == AUDIENCE_USER:
            wallet_service = WalletService(self.db)
            wallet = await wallet_service.get_or_create_wallet(user_id=reward.referrer_id)
            await wallet_service.credit(
                wallet.id,
                reward.reward_amount,
                "Referral reward",
                reference_id=str(reward.id),
                reference_type="REFERRAL",
            )
            wallet.referral_balance = float(wallet.referral_balance or 0) + float(reward.reward_amount)
        else:
            await DriverWalletService(self.db).credit_bonus(
                driver_id=reward.referrer_id,
                amount=reward.reward_amount,
                description="Referral reward",
            )

        reward.status = STATUS_PAID
        reward.paid_at = _utc_now()
        await self.db.flush()

    async def process_after_ride_completed(self, ride: Ride) -> None:
        # User (passenger) progress
        if ride.user_id:
            reward = await self.db.scalar(
                select(ReferralReward).where(
                    ReferralReward.audience == AUDIENCE_USER,
                    ReferralReward.referee_id == ride.user_id,
                    ReferralReward.status == STATUS_PENDING,
                )
            )
            if reward:
                reward.rides_completed = await self._count_completed_rides_for_user(ride.user_id)
                if reward.rides_completed >= reward.required_rides:
                    await self._pay_reward(reward)

        # Driver (captain) progress
        if ride.driver_id:
            reward = await self.db.scalar(
                select(ReferralReward).where(
                    ReferralReward.audience == AUDIENCE_DRIVER,
                    ReferralReward.referee_id == ride.driver_id,
                    ReferralReward.status == STATUS_PENDING,
                )
            )
            if reward:
                reward.rides_completed = await self._count_completed_rides_for_driver(ride.driver_id)
                if reward.rides_completed >= reward.required_rides:
                    await self._pay_reward(reward)

    async def list_rewards_for_referrer(self, audience: str, referrer_id: UUID) -> list[ReferralReward]:
        result = await self.db.execute(
            select(ReferralReward)
            .where(
                ReferralReward.audience == audience,
                ReferralReward.referrer_id == referrer_id,
            )
            .order_by(ReferralReward.created_at.desc())
        )
        return list(result.scalars().all())

    async def dashboard_for_user(self, user: User) -> dict:
        await self.ensure_default_programs()
        program = await self.get_program(AUDIENCE_USER)
        code = await self.ensure_user_invite_code(user)
        rewards = await self.list_rewards_for_referrer(AUDIENCE_USER, user.id)
        earned = sum(r.reward_amount for r in rewards if r.status == STATUS_PAID)
        pending = sum(1 for r in rewards if r.status == STATUS_PENDING)
        share = _format_share_message(
            program.share_message if program else None,
            code=code,
            reward_amount=float(program.reward_amount) if program else 0,
            required_rides=int(program.required_rides) if program else 0,
            audience=AUDIENCE_USER,
        )
        return {
            "enabled": bool(program and program.is_enabled),
            "program": self.serialize_program(program) if program else None,
            "inviteCode": code,
            "shareMessage": share,
            "stats": {
                "totalReferrals": len(rewards),
                "pendingReferrals": pending,
                "totalEarned": earned,
            },
            "referrals": [
                {
                    "id": str(r.id),
                    "status": r.status,
                    "requiredRides": r.required_rides,
                    "ridesCompleted": r.rides_completed,
                    "rewardAmount": r.reward_amount,
                    "createdAt": r.created_at.isoformat() if r.created_at else None,
                    "paidAt": r.paid_at.isoformat() if r.paid_at else None,
                }
                for r in rewards
            ],
            "hasAppliedCode": user.referred_by_id is not None
            or (await self._existing_reward_for_referee(AUDIENCE_USER, user.id)) is not None,
        }

    async def dashboard_for_driver(self, driver: Driver) -> dict:
        await self.ensure_default_programs()
        program = await self.get_program(AUDIENCE_DRIVER)
        code = await self.ensure_driver_invite_code(driver)
        rewards = await self.list_rewards_for_referrer(AUDIENCE_DRIVER, driver.id)
        earned = sum(r.reward_amount for r in rewards if r.status == STATUS_PAID)
        pending = sum(1 for r in rewards if r.status == STATUS_PENDING)
        share = _format_share_message(
            program.share_message if program else None,
            code=code,
            reward_amount=float(program.reward_amount) if program else 0,
            required_rides=int(program.required_rides) if program else 0,
            audience=AUDIENCE_DRIVER,
        )
        applied = await self._existing_reward_for_referee(AUDIENCE_DRIVER, driver.id)
        return {
            "enabled": bool(program and program.is_enabled),
            "program": self.serialize_program(program) if program else None,
            "inviteCode": code,
            "shareMessage": share,
            "stats": {
                "totalReferrals": len(rewards),
                "pendingReferrals": pending,
                "totalEarned": earned,
            },
            "referrals": [
                {
                    "id": str(r.id),
                    "status": r.status,
                    "requiredRides": r.required_rides,
                    "ridesCompleted": r.rides_completed,
                    "rewardAmount": r.reward_amount,
                    "createdAt": r.created_at.isoformat() if r.created_at else None,
                    "paidAt": r.paid_at.isoformat() if r.paid_at else None,
                }
                for r in rewards
            ],
            "hasAppliedCode": applied is not None,
        }

    async def _person_snapshot(self, audience: str, person_id: UUID) -> dict:
        if audience == AUDIENCE_USER:
            user = await self.db.get(User, person_id)
            if not user:
                return {"id": str(person_id), "name": "Unknown", "phone": "", "inviteCode": ""}
            return {
                "id": str(user.id),
                "name": f"{user.first_name} {user.last_name}".strip() or "User",
                "phone": user.phone or "",
                "inviteCode": user.referral_code or "",
            }
        driver = await self.db.get(Driver, person_id)
        if not driver:
            return {"id": str(person_id), "name": "Unknown", "phone": "", "inviteCode": ""}
        return {
            "id": str(driver.id),
            "name": f"{driver.first_name} {driver.last_name}".strip() or "Driver",
            "phone": driver.phone or "",
            "inviteCode": driver.invite_code or "",
        }

    async def refresh_reward_progress(self, reward: ReferralReward) -> ReferralReward:
        if reward.status != STATUS_PENDING:
            return reward
        if reward.audience == AUDIENCE_USER:
            reward.rides_completed = await self._count_completed_rides_for_user(reward.referee_id)
        else:
            reward.rides_completed = await self._count_completed_rides_for_driver(reward.referee_id)
        if reward.rides_completed >= reward.required_rides:
            await self._pay_reward(reward)
        else:
            await self.db.flush()
        return reward

    async def serialize_reward_admin(self, reward: ReferralReward) -> dict:
        referrer = await self._person_snapshot(reward.audience, reward.referrer_id)
        referee = await self._person_snapshot(reward.audience, reward.referee_id)
        remaining = max(0, int(reward.required_rides) - int(reward.rides_completed))
        return {
            "id": str(reward.id),
            "audience": reward.audience,
            "status": reward.status,
            "requiredRides": reward.required_rides,
            "ridesCompleted": reward.rides_completed,
            "ridesRemaining": remaining,
            "rewardAmount": reward.reward_amount,
            "willCreditWhen": (
                "Already credited"
                if reward.status == STATUS_PAID
                else (
                    "Cancelled"
                    if reward.status == STATUS_CANCELLED
                    else f"After {remaining} more ride(s) by referred person"
                )
            ),
            "referrer": referrer,
            "referee": referee,
            "createdAt": reward.created_at.isoformat() if reward.created_at else None,
            "paidAt": reward.paid_at.isoformat() if reward.paid_at else None,
            "updatedAt": reward.updated_at.isoformat() if reward.updated_at else None,
        }

    async def list_rewards_admin(
        self,
        *,
        audience: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        query = select(ReferralReward).order_by(ReferralReward.created_at.desc())
        if audience:
            query = query.where(ReferralReward.audience == audience.upper())
        if status:
            query = query.where(ReferralReward.status == status.upper())
        result = await self.db.execute(query)
        rewards = list(result.scalars().all())

        # Keep pending progress fresh for admin view
        for reward in rewards:
            if reward.status == STATUS_PENDING:
                await self.refresh_reward_progress(reward)

        return [await self.serialize_reward_admin(r) for r in rewards]

    async def admin_update_reward(self, reward_id: UUID, data: dict) -> dict:
        reward = await self.db.get(ReferralReward, reward_id)
        if not reward:
            raise NotFoundException("Referral record not found")

        if "required_rides" in data and data["required_rides"] is not None:
            rides = int(data["required_rides"])
            if rides < 1:
                raise ValidationException("required_rides must be at least 1")
            reward.required_rides = rides

        if "reward_amount" in data and data["reward_amount"] is not None:
            amount = float(data["reward_amount"])
            if amount < 0:
                raise ValidationException("reward_amount cannot be negative")
            if reward.status == STATUS_PAID:
                raise ValidationException("Cannot change amount after reward is already paid")
            reward.reward_amount = amount

        action = str(data.get("action") or "").strip().lower()
        status = str(data.get("status") or "").strip().upper()

        if action == "refresh" or status == "REFRESH":
            await self.refresh_reward_progress(reward)
        elif action == "pay_now" or status == "PAID":
            if reward.status == STATUS_CANCELLED:
                raise ValidationException("Cancelled referral cannot be paid")
            # Refresh count then force pay
            if reward.audience == AUDIENCE_USER:
                reward.rides_completed = await self._count_completed_rides_for_user(reward.referee_id)
            else:
                reward.rides_completed = await self._count_completed_rides_for_driver(reward.referee_id)
            await self._pay_reward(reward)
        elif action == "cancel" or status == "CANCELLED":
            if reward.status == STATUS_PAID:
                raise ValidationException("Paid referral cannot be cancelled")
            reward.status = STATUS_CANCELLED
            await self.db.flush()
        elif action == "reopen" or status == "PENDING":
            if reward.status == STATUS_PAID:
                raise ValidationException("Paid referral cannot be reopened")
            reward.status = STATUS_PENDING
            await self.refresh_reward_progress(reward)
        else:
            await self.db.flush()
            if reward.status == STATUS_PENDING:
                await self.refresh_reward_progress(reward)

        await self.db.refresh(reward)
        return await self.serialize_reward_admin(reward)
