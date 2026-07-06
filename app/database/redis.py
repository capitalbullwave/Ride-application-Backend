"""Backward-compatible Redis — use app.core.redis in new code."""
from app.core.redis import close_redis, get_redis, get_sync_redis, redis_client, sync_redis_client

__all__ = ["close_redis", "get_redis", "get_sync_redis", "redis_client", "sync_redis_client"]
