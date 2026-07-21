"""ORM models for driver shifts and selfie verification logs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import SelfieVerificationStatus, ShiftStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.drivers.models import Driver


class DriverShift(UUIDMixin, TimestampMixin, Base):
    """One work session. A fresh selfie is required for every new shift."""

    __tablename__ = "driver_shifts"
    __table_args__ = (
        Index("ix_driver_shifts_driver_status", "driver_id", "status"),
        Index("ix_driver_shifts_started_at", "started_at"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default=ShiftStatus.ACTIVE.value, nullable=False, index=True
    )
    selfie_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selfie_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    force_close_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verification_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="shifts")
    selfie_logs: Mapped[List["DriverSelfieLog"]] = relationship(
        "DriverSelfieLog",
        back_populates="shift",
        foreign_keys="DriverSelfieLog.shift_id",
    )


class DriverSelfieLog(UUIDMixin, TimestampMixin, Base):
    """Immutable audit trail for every selfie verification attempt."""

    __tablename__ = "driver_selfie_logs"
    __table_args__ = (
        Index("ix_driver_selfie_logs_driver_created", "driver_id", "created_at"),
        Index("ix_driver_selfie_logs_status", "status"),
    )

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_shifts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default=SelfieVerificationStatus.PENDING.value, nullable=False
    )
    matched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liveness_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    liveness_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    face_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    liveness_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Stored path or encrypted blob reference — never expose raw gallery uploads
    selfie_image_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    registered_image_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="live_camera", nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    consumed_for_shift: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="selfie_logs")
    shift: Mapped[Optional["DriverShift"]] = relationship(
        "DriverShift",
        back_populates="selfie_logs",
        foreign_keys=[shift_id],
    )
