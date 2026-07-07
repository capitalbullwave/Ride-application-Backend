"""Vehicle ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import VehicleStatus
from app.core.database import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.rides.models import Ride


class VehicleType(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_types"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    base_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    per_km_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    per_minute_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    waiting_charge_per_min: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    included_distance_km: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    included_hours: Mapped[float] = mapped_column(Float, default=4.0, nullable=False)
    per_hour_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    minimum_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cancellation_charge: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    service_group: Mapped[str] = mapped_column(String(20), default="ride", nullable=False, index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    driver_commission_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    vehicles: Mapped[List["Vehicle"]] = relationship("Vehicle", back_populates="vehicle_type")
    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="vehicle_type")


class Vehicle(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "vehicles"

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_types.id"), nullable=False
    )
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(30), nullable=False)
    license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    rc_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    insurance_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    insurance_expiry: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=VehicleStatus.PENDING.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    driver: Mapped["Driver"] = relationship("Driver", back_populates="vehicles")
    vehicle_type: Mapped["VehicleType"] = relationship("VehicleType", back_populates="vehicles")
    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="vehicle")
