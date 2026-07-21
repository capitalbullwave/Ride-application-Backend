"""Corporate B2B ORM models — companies, employees, ride policies."""
import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import CompanyEmployeeStatus, CompanyStatus
from app.core.database import Base, TimestampMixin, UUIDMixin

# Runtime import so SQLAlchemy can resolve relationship("AdminUser")
from app.admin.models import AdminUser  # noqa: F401

if TYPE_CHECKING:
    from app.rides.models import Ride
    from app.users.models import User


class Company(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_status_created", "status", "created_at"),
    )

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    gst_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    pan_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    company_size: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="India", nullable=False)
    contact_person: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_limit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    wallet_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=CompanyStatus.PENDING.value, nullable=False, index=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(default=1, nullable=False)

    employees: Mapped[List["CompanyEmployee"]] = relationship(
        "CompanyEmployee", back_populates="company", cascade="all, delete-orphan"
    )
    policy: Mapped[Optional["CompanyPolicy"]] = relationship(
        "CompanyPolicy", back_populates="company", uselist=False, cascade="all, delete-orphan"
    )
    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="company")
    approver: Mapped[Optional["AdminUser"]] = relationship("AdminUser", foreign_keys=[approved_by])


class CompanyEmployee(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "company_employees"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_company_employees_company_user"),
        UniqueConstraint("company_id", "employee_code", name="uq_company_employees_code"),
        Index("ix_company_employees_user_status", "user_id", "status"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_code: Mapped[str] = mapped_column(String(50), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ride_limit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=CompanyEmployeeStatus.ACTIVE.value, nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship("Company", back_populates="employees")
    user: Mapped["User"] = relationship("User", back_populates="company_memberships")
    rides: Mapped[List["Ride"]] = relationship("Ride", back_populates="employee")


class CompanyPolicy(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "company_policies"
    __table_args__ = (UniqueConstraint("company_id", name="uq_company_policies_company"),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allowed_vehicle_types: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    max_ride_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    office_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    office_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    working_days: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purpose_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped["Company"] = relationship("Company", back_populates="policy")
