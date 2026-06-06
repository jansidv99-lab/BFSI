import time

import fakeredis
import pytest
from fastapi import HTTPException
from prometheus_client import Counter

from api.metrics import ANALYTICS_REQUESTS, CHAT_REQUESTS, RATE_LIMIT_HITS


def _counter_value(counter, labels=None):
    """Read current _total sample value from a prometheus Counter."""
    for family in counter.collect():
        for sample in family.samples:
            if not sample.name.endswith("_total"):
                continue
            if labels and not all(sample.labels.get(k) == v for k, v in labels.items()):
                continue
            return sample.value
    return 0.0


# ── Counter definitions ───────────────────────────────────────────────────────

def test_counters_are_counter_type():
    assert isinstance(CHAT_REQUESTS, Counter)
    assert isinstance(ANALYTICS_REQUESTS, Counter)
    assert isinstance(RATE_LIMIT_HITS, Counter)


def test_rate_limit_hit_has_endpoint_label():
    assert "endpoint" in RATE_LIMIT_HITS._labelnames


# ── Counter behaviour ─────────────────────────────────────────────────────────

def test_chat_counter_increments():
    before = _counter_value(CHAT_REQUESTS)
    CHAT_REQUESTS.inc()
    assert _counter_value(CHAT_REQUESTS) == before + 1.0


def test_rate_limit_hit_increments_on_429(monkeypatch):
    import api.rate_limit as rl
    from api.rate_limit import SlidingWindowRateLimiter

    fake_r = fakeredis.FakeRedis(decode_responses=True)
    fixed = [time.time()]
    monkeypatch.setattr(rl.time, "time", lambda: fixed[0])

    limiter = SlidingWindowRateLimiter(fake_r, limit=1, window_seconds=60)
    limiter.check("chat:user99")  # passes — no counter increment

    before = _counter_value(RATE_LIMIT_HITS, {"endpoint": "chat"})
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("chat:user99")  # blocked → RATE_LIMIT_HITS["chat"] increments
    assert exc_info.value.status_code == 429
    assert _counter_value(RATE_LIMIT_HITS, {"endpoint": "chat"}) == before + 1.0
