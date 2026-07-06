"""Backward-compatible ORM base — use app.core.database in new code."""
from app.core.database import AuditMixin, Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

__all__ = ["AuditMixin", "Base", "SoftDeleteMixin", "TimestampMixin", "UUIDMixin"]
