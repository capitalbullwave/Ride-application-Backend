"""User ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import UserRole
from app.core.database import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.auth.models import AuthDevice, UserSession
    from app.coupons.models import ReferralCode
    from app.notifications.models import Notification
    from app.ratings.models import Rating
    from app.rides.models import Ride
    from app.support.models import SupportTicket
    from app.wallet.models import UserBankAccount, Wallet, WithdrawalRequest


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_active_verified", "is_active", "is_verified"),
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_photo: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    otp: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fcm_token: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    device_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_login_device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)
    referred_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    google_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    token_version: Mapped[int] = mapped_column(default=1, nullable=False)

    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="user", foreign_keys="Ride.user_id")
    wallet: Mapped[Optional["Wallet"]] = relationship("Wallet", back_populates="user", uselist=False)
    bank_accounts: Mapped[List["UserBankAccount"]] = relationship(
        "UserBankAccount", back_populates="user"
    )
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship(
        "WithdrawalRequest", back_populates="user"
    )
    saved_addresses: Mapped[List["SavedAddress"]] = relationship("SavedAddress", back_populates="user")
    ratings_given: Mapped[List["Rating"]] = relationship(
        "Rating", back_populates="user", foreign_keys="Rating.user_id"
    )
    notifications: Mapped[List["Notification"]] = relationship("Notification", back_populates="user")
    support_tickets: Mapped[List["SupportTicket"]] = relationship("SupportTicket", back_populates="user")
    sessions: Mapped[List["UserSession"]] = relationship("UserSession", back_populates="user")
    devices: Mapped[List["AuthDevice"]] = relationship("AuthDevice", back_populates="user")
    referral_codes: Mapped[List["ReferralCode"]] = relationship("ReferralCode", back_populates="user")
    referred_by: Mapped[Optional["User"]] = relationship("User", remote_side="User.id", foreign_keys=[referred_by_id])


class SavedAddress(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "saved_addresses"
    __table_args__ = (Index("ix_saved_addresses_user_default", "user_id", "is_default"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="saved_addresses")
