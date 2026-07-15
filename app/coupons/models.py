"""Coupon and referral ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.rides.models import Ride
    from app.users.models import User


class PromoCode(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)
    discount_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_discount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_order_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(nullable=False)
    valid_until: Mapped[datetime] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="promo_code")


class ReferralCode(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "referral_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    reward_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="referral_codes")


class ReferralProgram(UUIDMixin, TimestampMixin, Base):
    """Admin-managed Refer & Earn rules (one row per audience)."""

    __tablename__ = "referral_programs"
    __table_args__ = (UniqueConstraint("audience", name="uq_referral_programs_audience"),)

    audience: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # USER | DRIVER
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    required_rides: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reward_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="Refer & Earn")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    share_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ReferralReward(UUIDMixin, TimestampMixin, Base):
    """Tracks a referrer → referee relationship and payout progress."""

    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint("audience", "referee_id", name="uq_referral_rewards_audience_referee"),
    )

    audience: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # USER | DRIVER
    program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("referral_programs.id", ondelete="SET NULL"), nullable=True
    )
    referrer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    referee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    required_rides: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_amount: Mapped[float] = mapped_column(Float, nullable=False)
    rides_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
