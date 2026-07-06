"""Shared FastAPI dependencies injected across all modules."""
from app.dependencies.database import get_db
from app.dependencies.pagination import PaginationParams, get_pagination

__all__ = ["get_db", "PaginationParams", "get_pagination"]
