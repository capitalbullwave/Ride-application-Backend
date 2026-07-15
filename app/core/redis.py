"""Redis async/sync clients for caching, sessions, and driver geo-index."""
from __future__ import annotations

import time

import redis.asyncio as aioredis
from redis import Redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

redis_client: aioredis.Redis | None = None
sync_redis_client: Redis | None = None

# When Redis is unreachable, skip reconnect attempts briefly so ride dispatch
# and location updates stay fast on the DB/WebSocket path.
_REDIS_DOWN_UNTIL = 0.0
_REDIS_DOWN_COOLDOWN_SEC = 20.0


async def get_redis() -> aioredis.Redis | None:
    """Return a live Redis client, or None if Redis is down / timed out."""
    global redis_client, _REDIS_DOWN_UNTIL

    now = time.monotonic()
    if now < _REDIS_DOWN_UNTIL:
        return None

    try:
        if redis_client is None:
            redis_client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.4,
                socket_timeout=0.4,
                retry_on_timeout=False,
                health_check_interval=30,
            )
        await redis_client.ping()
        return redis_client
    except Exception as exc:
        _REDIS_DOWN_UNTIL = time.monotonic() + _REDIS_DOWN_COOLDOWN_SEC
        logger.warning(
            "redis_unavailable",
            error=str(exc),
            cooldown_sec=_REDIS_DOWN_COOLDOWN_SEC,
        )
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
            redis_client = None
        return None


def get_sync_redis() -> Redis | None:
    global sync_redis_client, _REDIS_DOWN_UNTIL
    if time.monotonic() < _REDIS_DOWN_UNTIL:
        return None
    try:
        if sync_redis_client is None:
            sync_redis_client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.4,
                socket_timeout=0.4,
            )
        sync_redis_client.ping()
        return sync_redis_client
    except Exception as exc:
        _REDIS_DOWN_UNTIL = time.monotonic() + _REDIS_DOWN_COOLDOWN_SEC
        logger.warning("redis_sync_unavailable", error=str(exc))
        sync_redis_client = None
        return None


async def close_redis() -> None:
    global redis_client
    if redis_client:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None
