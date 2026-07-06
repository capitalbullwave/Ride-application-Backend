"""Platform settings and pricing ORM models."""
import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.vehicles.models import VehicleType


class City(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cities"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), default="India", nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata", nullable=False)

    pricing_rules: Mapped[List["PricingRule"]] = relationship("PricingRule", back_populates="city")


class PricingRule(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pricing_rules"

    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_types.id", ondelete="CASCADE"), nullable=False
    )
    base_fare: Mapped[float] = mapped_column(Float, nullable=False)
    per_km_rate: Mapped[float] = mapped_column(Float, nullable=False)
    per_minute_rate: Mapped[float] = mapped_column(Float, nullable=False)
    waiting_charge_per_min: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    night_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    peak_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    city: Mapped["City"] = relationship("City", back_populates="pricing_rules")
    vehicle_type: Mapped["VehicleType"] = relationship("VehicleType")


class AppSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SystemConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "system_configs"

    config_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    config_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
