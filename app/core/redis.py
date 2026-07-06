"""Redis async/sync clients for caching, sessions, and driver geo-index."""
import redis.asyncio as aioredis
from redis import Redis

from app.core.config import settings

redis_client: aioredis.Redis | None = None
sync_redis_client: Redis | None = None


async def get_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return redis_client


def get_sync_redis() -> Redis:
    global sync_redis_client
    if sync_redis_client is None:
        sync_redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return sync_redis_client


async def close_redis() -> None:
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None
