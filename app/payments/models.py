"""Payment ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import PaymentStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.rides.models import Ride


class Payment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_status_created", "status", "created_at"),)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING.value, nullable=False, index=True)
    gateway_transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    gateway_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    refund_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    invoice_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    ride: Mapped["Ride"] = relationship("Ride", back_populates="payment")
