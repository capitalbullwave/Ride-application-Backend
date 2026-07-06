"""Admin panel ORM models."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.auth.models import UserSession


class AdminUser(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped["AdminRole"] = relationship("AdminRole", back_populates="admin_users")
    logs: Mapped[List["AdminLog"]] = relationship("AdminLog", back_populates="admin_user")
    sessions: Mapped[List["UserSession"]] = relationship("UserSession", back_populates="admin_user")


class AdminRole(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "admin_roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    admin_users: Mapped[List["AdminUser"]] = relationship("AdminUser", back_populates="role")
    permissions: Mapped[List["AdminPermission"]] = relationship(
        "AdminPermission", secondary="admin_role_permissions", back_populates="roles"
    )


class AdminPermission(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "admin_permissions"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    codename: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    roles: Mapped[List["AdminRole"]] = relationship(
        "AdminRole", secondary="admin_role_permissions", back_populates="permissions"
    )


class AdminRolePermission(Base):
    __tablename__ = "admin_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_permissions.id", ondelete="CASCADE"), primary_key=True
    )


class AdminLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "admin_logs"

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    admin_user: Mapped["AdminUser"] = relationship("AdminUser", back_populates="logs")
