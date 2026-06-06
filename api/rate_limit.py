import logging
import os
import time
import uuid

import redis
from fastapi import HTTPException

from api.metrics import RATE_LIMIT_HITS

logger = logging.getLogger(__name__)

_redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    decode_responses=True,
)


class SlidingWindowRateLimiter:
    """ZSET-based sliding window rate limiter.

    Non-atomic (educational scope): ZREMRANGEBYSCORE+ZCARD are pipelined, but
    ZADD is a separate call. A TOCTOU race can allow a burst of N+1 under high
    concurrency. Production upgrade: replace with a Lua script via
    redis.register_script() to make check+add atomic.
    """

    def __init__(self, redis_client: redis.Redis, limit: int, window_seconds: int) -> None:
        self.r = redis_client
        self.limit = limit
        self.window = window_seconds

    def check(self, key: str) -> None:
        """Raise HTTP 429 if the key is over limit. Fail-open on Redis errors."""
        try:
            now = time.time()
            pipe = self.r.pipeline()
            pipe.zremrangebyscore(key, "-inf", now - self.window)
            pipe.zcard(key)
            _, count = pipe.execute()
            if count >= self.limit:
                RATE_LIMIT_HITS.labels(endpoint=key.split(":")[0]).inc()
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please slow down.",
                    headers={"Retry-After": str(self.window)},
                )
            self.r.zadd(key, {str(uuid.uuid4()): now})
            self.r.expire(key, self.window)
        except HTTPException:
            raise
        except redis.RedisError as exc:
            logger.warning("Rate limiter Redis error key=%r: %s — failing open", key, exc)


class TokenBucket:
    """Redis HASH-based token bucket (educational — not wired to routes).

    Stores {tokens, last_refill} per key. Refills at refill_rate tokens/second
    up to capacity. Non-atomic for clarity; production use requires a Lua script.

    Args:
        capacity:    Maximum tokens the bucket can hold.
        refill_rate: Tokens added per second.
    """

    def __init__(self, redis_client: redis.Redis, capacity: float, refill_rate: float) -> None:
        self.r = redis_client
        self.capacity = capacity
        self.refill_rate = refill_rate

    def check(self, key: str) -> None:
        """Raise HTTP 429 if no token available. Fail-open on Redis errors."""
        try:
            now = time.time()
            data = self.r.hgetall(key)
            if data:
                elapsed = now - float(data["last_refill"])
                tokens = min(self.capacity, float(data["tokens"]) + elapsed * self.refill_rate)
            else:
                tokens = self.capacity
            if tokens < 1:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please slow down.",
                    headers={"Retry-After": str(int(1 / self.refill_rate) + 1)},
                )
            self.r.hset(key, mapping={"tokens": tokens - 1, "last_refill": now})
            self.r.expire(key, int(self.capacity / self.refill_rate) + 1)
        except HTTPException:
            raise
        except redis.RedisError as exc:
            logger.warning("TokenBucket Redis error key=%r: %s — failing open", key, exc)


# Module-level instances — redis.Redis() connects lazily (no TCP until first command)
chat_limiter = SlidingWindowRateLimiter(_redis_client, limit=20, window_seconds=60)
analytics_limiter = SlidingWindowRateLimiter(_redis_client, limit=5, window_seconds=60)
login_limiter = SlidingWindowRateLimiter(_redis_client, limit=10, window_seconds=60)
data_chat_limiter = SlidingWindowRateLimiter(_redis_client, limit=10, window_seconds=60)
