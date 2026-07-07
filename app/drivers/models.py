"""Driver ORM models."""
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import DriverStatus, KYCStatus
from app.core.database import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.auth.models import AuthDevice, UserSession
    from app.notifications.models import Notification
    from app.ratings.models import Rating
    from app.rides.models import Ride
    from app.support.models import SupportTicket
    from app.vehicles.models import Vehicle
    from app.commission.models import DriverWallet
    from app.wallet.models import Wallet, WithdrawalRequest


class Driver(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "drivers"
    __table_args__ = (
        Index("ix_drivers_status_active", "status", "is_active"),
        Index("ix_drivers_kyc_status", "kyc_status"),
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_photo: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    license_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pin_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    kyc_status: Mapped[str] = mapped_column(String(20), default=KYCStatus.PENDING.value, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=DriverStatus.OFFLINE.value, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    otp: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_rides: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fcm_token: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_version: Mapped[int] = mapped_column(default=1, nullable=False)

    documents: Mapped[List["DriverDocument"]] = relationship("DriverDocument", back_populates="driver")
    vehicles: Mapped[List["Vehicle"]] = relationship("Vehicle", back_populates="driver")
    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="driver", foreign_keys="Ride.driver_id")
    wallet: Mapped[Optional["Wallet"]] = relationship("Wallet", back_populates="driver", uselist=False)
    driver_wallet: Mapped[Optional["DriverWallet"]] = relationship(
        "DriverWallet", back_populates="driver", uselist=False
    )
    location: Mapped[Optional["DriverLocation"]] = relationship(
        "DriverLocation", back_populates="driver", uselist=False
    )
    ratings_received: Mapped[List["Rating"]] = relationship(
        "Rating", back_populates="driver", foreign_keys="Rating.driver_id"
    )
    bank_accounts: Mapped[List["DriverBankAccount"]] = relationship("DriverBankAccount", back_populates="driver")
    sessions: Mapped[List["UserSession"]] = relationship("UserSession", back_populates="driver")
    devices: Mapped[List["AuthDevice"]] = relationship("AuthDevice", back_populates="driver")
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship("WithdrawalRequest", back_populates="driver")
    notifications: Mapped[List["Notification"]] = relationship("Notification", back_populates="driver")
    support_tickets: Mapped[List["SupportTicket"]] = relationship("SupportTicket", back_populates="driver")
    emergency_contacts: Mapped[List["DriverEmergencyContact"]] = relationship(
        "DriverEmergencyContact", back_populates="driver", cascade="all, delete-orphan"
    )


class DriverEmergencyContact(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_emergency_contacts"
    __table_args__ = (Index("ix_driver_emergency_contacts_driver", "driver_id"),)

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    relation: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="emergency_contacts")


class DriverDocument(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_documents"
    __table_args__ = (Index("ix_driver_documents_type_status", "driver_id", "document_type", "status"),)

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    document_url: Mapped[str] = mapped_column(String(500), nullable=False)
    document_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=KYCStatus.PENDING.value, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="documents")


class DriverLocation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_locations"

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    heading: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    speed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="location")


class DriverBankAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_bank_accounts"
    __table_args__ = (Index("ix_driver_bank_primary", "driver_id", "is_primary"),)

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_holder_name: Mapped[str] = mapped_column(String(150), nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(30), nullable=False)
    ifsc_code: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    upi_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="bank_accounts")
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship("WithdrawalRequest", back_populates="bank_account")
