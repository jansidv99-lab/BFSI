import time

import fakeredis
import pytest
import redis as redis_lib
from fastapi import HTTPException

from api.rate_limit import SlidingWindowRateLimiter, TokenBucket


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


# ── SlidingWindowRateLimiter ──────────────────────────────────────────────────

def test_sliding_window_allows_under_limit(fake_redis):
    limiter = SlidingWindowRateLimiter(fake_redis, limit=3, window_seconds=60)
    limiter.check("sw:1")
    limiter.check("sw:1")
    limiter.check("sw:1")  # count was 0, 1, 2 before each add — all under limit


def test_sliding_window_blocks_at_limit(fake_redis, monkeypatch):
    import api.rate_limit as rl
    fixed = [time.time()]
    monkeypatch.setattr(rl.time, "time", lambda: fixed[0])

    limiter = SlidingWindowRateLimiter(fake_redis, limit=3, window_seconds=60)
    limiter.check("sw:2")
    limiter.check("sw:2")
    limiter.check("sw:2")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("sw:2")  # count=3 >= limit=3 → 429
    assert exc_info.value.status_code == 429


def test_sliding_window_retry_after_header(fake_redis):
    limiter = SlidingWindowRateLimiter(fake_redis, limit=1, window_seconds=60)
    limiter.check("sw:3")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("sw:3")
    assert exc_info.value.headers["Retry-After"] == "60"


def test_sliding_window_resets_after_window(fake_redis, monkeypatch):
    import api.rate_limit as rl
    limiter = SlidingWindowRateLimiter(fake_redis, limit=2, window_seconds=60)
    limiter.check("sw:4")
    limiter.check("sw:4")

    # Advance time past the 60-second window so old entries fall out
    real_time = time.time
    monkeypatch.setattr(rl.time, "time", lambda: real_time() + 61)
    limiter.check("sw:4")  # must not raise — old entries expired


def test_sliding_window_fails_open_on_redis_error(monkeypatch):
    bad_redis = fakeredis.FakeRedis(decode_responses=True)

    class _BrokenPipeline:
        def zremrangebyscore(self, *a, **kw): pass
        def zcard(self, *a, **kw): pass
        def execute(self): raise redis_lib.ConnectionError("Redis down")

    monkeypatch.setattr(bad_redis, "pipeline", lambda: _BrokenPipeline())
    limiter = SlidingWindowRateLimiter(bad_redis, limit=1, window_seconds=60)
    limiter.check("sw:5")  # must not raise — fail open


# ── TokenBucket ───────────────────────────────────────────────────────────────

def test_token_bucket_allows_under_capacity(fake_redis):
    bucket = TokenBucket(fake_redis, capacity=3.0, refill_rate=1.0)
    bucket.check("tb:1")
    bucket.check("tb:1")
    bucket.check("tb:1")  # 3 tokens consumed — all succeed


def test_token_bucket_blocks_when_empty(fake_redis, monkeypatch):
    import api.rate_limit as rl
    # Freeze time so no refill occurs between calls
    fixed = [time.time()]
    monkeypatch.setattr(rl.time, "time", lambda: fixed[0])

    bucket = TokenBucket(fake_redis, capacity=2.0, refill_rate=1.0)
    bucket.check("tb:2")
    bucket.check("tb:2")
    with pytest.raises(HTTPException) as exc_info:
        bucket.check("tb:2")  # 0 tokens left, elapsed=0 → no refill → 429
    assert exc_info.value.status_code == 429


def test_token_bucket_refills_over_time(fake_redis, monkeypatch):
    import api.rate_limit as rl

    t = [time.time()]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])

    bucket = TokenBucket(fake_redis, capacity=1.0, refill_rate=1.0)
    bucket.check("tb:3")  # consume the 1 token

    t[0] += 2.0  # advance 2 seconds — refills to min(1.0, 0 + 2*1) = 1.0
    bucket.check("tb:3")  # must succeed — token refilled
