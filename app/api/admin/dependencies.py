"""Admin module dependencies."""
from app.auth.dependencies import get_current_admin, get_current_token

__all__ = ["get_current_admin", "get_current_token"]
