"""Rating ORM models."""
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import RaterType
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.drivers.models import Driver
    from app.rides.models import Ride
    from app.users.models import User


class Rating(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("ride_id", "rater_type", name="uq_ratings_ride_rater"),)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rides.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False
    )
    rater_type: Mapped[str] = mapped_column(String(10), default=RaterType.USER.value, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ride: Mapped["Ride"] = relationship("Ride", back_populates="ratings")
    user: Mapped["User"] = relationship("User", back_populates="ratings_given", foreign_keys=[user_id])
    driver: Mapped["Driver"] = relationship("Driver", back_populates="ratings_received", foreign_keys=[driver_id])
