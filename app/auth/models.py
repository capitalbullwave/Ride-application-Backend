"""Auth session and device ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import DevicePlatform
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.admin.models import AdminUser
    from app.drivers.models import Driver
    from app.users.models import User


class UserSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_active_expires", "is_active", "expires_at"),)

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    admin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_devices.id", ondelete="SET NULL"), nullable=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="sessions")
    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="sessions")
    admin_user: Mapped[Optional["AdminUser"]] = relationship("AdminUser", back_populates="sessions")
    device: Mapped[Optional["AuthDevice"]] = relationship("AuthDevice", back_populates="sessions")


class AuthDevice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "auth_devices"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    device_type: Mapped[str] = mapped_column(String(20), default=DevicePlatform.ANDROID.value, nullable=False)
    fcm_token: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_active_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="devices")
    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="devices")
    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="device")


class OtpLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "otp_logs"
    __table_args__ = (Index("ix_otp_logs_phone_created", "phone", "created_at"),)

    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    otp_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
