"""Backward-compatible settings import — use app.core.config in new code."""
from app.core.config import Settings, get_settings, settings

__all__ = ["Settings", "get_settings", "settings"]
