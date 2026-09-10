from collections.abc import AsyncGenerator

from redis.asyncio import Redis

from app.core.config import settings


def get_redis_client() -> Redis:
    """One Redis connection per caller, matching `redis.asyncio`'s own guidance that a
    `Redis` instance is a connection *pool* meant to be created once and reused, not
    opened per call - callers (a Celery task, a request handler, a WebSocket
    connection) own the client's lifetime and should call `.aclose()` when done,
    mirroring `async_session_factory`'s "caller owns the session" pattern elsewhere
    in this repo.
    """
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI dependency wrapper around `get_redis_client` - closes the client when
    the request (or WebSocket connection) ends, same lifetime FastAPI already manages
    for `get_session`/`get_timescale_session`.
    """
    redis = get_redis_client()
    try:
        yield redis
    finally:
        await redis.aclose()
