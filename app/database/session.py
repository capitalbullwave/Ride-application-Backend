"""Backward-compatible session — use app.core.database in new code."""
from app.core.database import (
    AsyncSessionLocal,
    SyncSessionLocal,
    async_engine,
    get_db,
    sync_engine,
)

__all__ = ["AsyncSessionLocal", "SyncSessionLocal", "async_engine", "get_db", "sync_engine"]
