"""Commission and driver wallet ORM models."""
import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.admin.models import AdminUser
    from app.drivers.models import Driver
    from app.rides.models import Ride


class CommissionSettings(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "commission_settings"
    __table_args__ = (Index("ix_commission_settings_active", "is_active"),)

    driver_commission_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )

    updated_by_admin: Mapped[Optional["AdminUser"]] = relationship("AdminUser")


class DriverWallet(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_wallet"
    __table_args__ = (UniqueConstraint("driver_id", name="uq_driver_wallet_driver"),)

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    available_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pending_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    lifetime_earnings: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="driver_wallet")
    transactions: Mapped[List["DriverWalletTransaction"]] = relationship(
        "DriverWalletTransaction", back_populates="wallet"
    )


class DriverWalletTransaction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "driver_wallet_transactions"
    __table_args__ = (
        Index("ix_driver_wallet_tx_driver_created", "driver_id", "created_at"),
        UniqueConstraint("ride_id", name="uq_driver_wallet_tx_ride_credit"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ride_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    balance_after_transaction: Mapped[float] = mapped_column(Float, nullable=False)

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_wallet.id", ondelete="CASCADE"), nullable=False, index=True
    )

    driver: Mapped["Driver"] = relationship("Driver")
    ride: Mapped[Optional["Ride"]] = relationship("Ride")
    wallet: Mapped["DriverWallet"] = relationship("DriverWallet", back_populates="transactions")


class CompanyRevenueLedger(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "company_revenue_ledger"
    __table_args__ = (UniqueConstraint("ride_id", name="uq_company_revenue_ride"),)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    ride: Mapped["Ride"] = relationship("Ride")
