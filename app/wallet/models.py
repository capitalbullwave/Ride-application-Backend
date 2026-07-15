"""Wallet ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import WithdrawalStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.drivers.models import Driver, DriverBankAccount
    from app.users.models import User


class UserBankAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_bank_accounts"
    __table_args__ = (Index("ix_user_bank_primary", "user_id", "is_primary"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_holder_name: Mapped[str] = mapped_column(String(150), nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(50), nullable=False)
    ifsc_code: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    upi_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="bank_accounts")
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship(
        "WithdrawalRequest", back_populates="user_bank_account"
    )


class Wallet(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_wallet_user"),
        UniqueConstraint("driver_id", name="uq_wallet_driver"),
        CheckConstraint(
            "(user_id IS NOT NULL AND driver_id IS NULL) OR (user_id IS NULL AND driver_id IS NOT NULL)",
            name="ck_wallet_owner_xor",
        ),
    )

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bonus_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    referral_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="wallet")
    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="wallet")
    transactions: Mapped[List["WalletTransaction"]] = relationship("WalletTransaction", back_populates="wallet")
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship("WithdrawalRequest", back_populates="wallet")


class WalletTransaction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (Index("ix_wallet_tx_wallet_created", "wallet_id", "created_at"),)

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    balance_before: Mapped[float] = mapped_column(Float, nullable=False)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="transactions")


class WalletTopUpPayment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "wallet_topup_payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    razorpay_order_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    gateway_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    wallet_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallet_transactions.id", ondelete="SET NULL"), nullable=True
    )


class WithdrawalRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "withdrawal_requests"
    __table_args__ = (
        Index("ix_withdrawals_driver_status", "driver_id", "status"),
        Index("ix_withdrawals_user_status", "user_id", "status"),
    )

    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_bank_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    user_bank_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_bank_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=WithdrawalStatus.PENDING.value, nullable=False, index=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    processed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="withdrawals")
    user: Mapped[Optional["User"]] = relationship("User", back_populates="withdrawals")
    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="withdrawals")
    bank_account: Mapped[Optional["DriverBankAccount"]] = relationship(
        "DriverBankAccount", back_populates="withdrawals"
    )
    user_bank_account: Mapped[Optional["UserBankAccount"]] = relationship(
        "UserBankAccount", back_populates="withdrawals"
    )
