"""Ride ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ActorType, PaymentMethod, RideEventType, RideStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.coupons.models import PromoCode
    from app.drivers.models import Driver
    from app.payments.models import Payment
    from app.ratings.models import Rating
    from app.users.models import User
    from app.vehicles.models import Vehicle, VehicleType


class Ride(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "rides"
    __table_args__ = (
        Index("ix_rides_status_created", "status", "created_at"),
        Index("ix_rides_user_status", "user_id", "status"),
        Index("ix_rides_driver_status", "driver_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    vehicle_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True
    )
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_types.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default=RideStatus.REQUESTED.value, nullable=False, index=True)
    pickup_address: Mapped[str] = mapped_column(String(500), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_address: Mapped[str] = mapped_column(String(500), nullable=False)
    dropoff_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_lng: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_distance_km: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_duration_min: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_duration_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    final_fare: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    driver_commission_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    driver_earning: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    company_earning: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    base_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    distance_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    time_fare: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    waiting_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    night_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    peak_charges: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    platform_fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    promo_discount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    wallet_deduction: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), default=PaymentMethod.CASH.value, nullable=False)
    ride_otp: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    promo_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    route_polyline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="rides", foreign_keys=[user_id])
    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="rides", foreign_keys=[driver_id])
    vehicle: Mapped[Optional["Vehicle"]] = relationship("Vehicle", back_populates="rides")
    vehicle_type: Mapped["VehicleType"] = relationship("VehicleType", back_populates="rides")
    promo_code: Mapped[Optional["PromoCode"]] = relationship("PromoCode", back_populates="rides")
    tracking: Mapped[List["RideTracking"]] = relationship("RideTracking", back_populates="ride")
    events: Mapped[List["RideEvent"]] = relationship("RideEvent", back_populates="ride", order_by="RideEvent.created_at")
    payment: Mapped[Optional["Payment"]] = relationship("Payment", back_populates="ride", uselist=False)
    ratings: Mapped[List["Rating"]] = relationship("Rating", back_populates="ride")
    chat_messages: Mapped[List["ChatMessage"]] = relationship("ChatMessage", back_populates="ride")


class RideTracking(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ride_tracking"
    __table_args__ = (Index("ix_ride_tracking_ride_created", "ride_id", "created_at"),)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heading: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    ride: Mapped["Ride"] = relationship("Ride", back_populates="tracking")


class RideEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ride_events"
    __table_args__ = (Index("ix_ride_events_ride_type", "ride_id", "event_type"),)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), default=ActorType.SYSTEM.value, nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    ride: Mapped["Ride"] = relationship("Ride", back_populates="events")


class ChatMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(default=False, nullable=False)

    ride: Mapped["Ride"] = relationship("Ride", back_populates="chat_messages")
