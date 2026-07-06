"""Student pass and subscription ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import KYCStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.users.models import User


class SubscriptionPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "subscription_plans"

    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    period_label: Mapped[str] = mapped_column(String(30), default="month", nullable=False)
    benefits_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ride_discount_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_popular: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)

    subscriptions: Mapped[list["UserSubscription"]] = relationship(back_populates="plan")
    payments: Mapped[list["SubscriptionPayment"]] = relationship(back_populates="plan")


class UserSubscription(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_subscriptions_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped["SubscriptionPlan"] = relationship(back_populates="subscriptions")


class SubscriptionPayment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "subscription_payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    razorpay_order_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    gateway_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    plan: Mapped["SubscriptionPlan"] = relationship(back_populates="payments")


class StudentPass(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "student_passes"
    __table_args__ = (UniqueConstraint("user_id", name="uq_student_passes_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aadhar_number: Mapped[str] = mapped_column(String(12), nullable=False)
    college_name: Mapped[str] = mapped_column(String(200), nullable=False)
    aadhar_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    student_id_photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=KYCStatus.PENDING.value, nullable=False, index=True
    )
    discount_percent: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
