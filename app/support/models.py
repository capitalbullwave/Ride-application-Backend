"""Support ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import SupportTicketPriority, SupportTicketStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.drivers.models import Driver
    from app.users.models import User


class SupportTicket(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (Index("ix_support_tickets_status_priority", "status", "priority"),)

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=SupportTicketStatus.OPEN.value, nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(20), default=SupportTicketPriority.MEDIUM.value, nullable=False)
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="support_tickets")
    driver: Mapped[Optional["Driver"]] = relationship("Driver", back_populates="support_tickets")
    replies: Mapped[List["SupportTicketReply"]] = relationship("SupportTicketReply", back_populates="ticket")


class SupportTicketReply(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_ticket_replies"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    ticket: Mapped["SupportTicket"] = relationship("SupportTicket", back_populates="replies")


class Faq(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "faqs"
    __table_args__ = (Index("ix_faqs_category_active", "category", "is_active"),)

    category: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
