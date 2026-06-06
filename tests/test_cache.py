import fakeredis
import pytest
import redis as redis_lib

import api.cache as cache_mod
from api.cache import cache_get, cache_set


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_mod, "_redis_client", fr)
    return fr


@pytest.fixture(autouse=True)
def fixed_embed(monkeypatch):
    monkeypatch.setattr(cache_mod, "_embed", lambda text: [1.0, 0.0, 0.0])


def test_cache_miss_returns_none():
    assert cache_get("analytics", ["what is nifty"]) is None


def test_set_then_get_returns_value():
    cache_set("analytics", ["what is nifty"], "result_json", 3600)
    assert cache_get("analytics", ["what is nifty"]) == "result_json"


def test_semantically_similar_returns_cached(monkeypatch):
    cache_set("analytics", ["show my profit"], "result_json", 3600)
    monkeypatch.setattr(cache_mod, "_embed", lambda text: [0.999, 0.045, 0.0])
    assert cache_get("analytics", ["what is my P&L"]) == "result_json"


def test_dissimilar_query_returns_none(monkeypatch):
    cache_set("analytics", ["show my profit"], "result_json", 3600)
    monkeypatch.setattr(cache_mod, "_embed", lambda text: [0.0, 1.0, 0.0])
    assert cache_get("analytics", ["something completely different"]) is None


def test_fail_open_on_embed_error(monkeypatch):
    monkeypatch.setattr(cache_mod, "_embed", lambda text: [])
    assert cache_get("analytics", ["any question"]) is None


def test_fail_open_on_redis_error_get(monkeypatch):
    class _BrokenRedis:
        def scan(self, *a, **kw): raise redis_lib.ConnectionError("down")

    monkeypatch.setattr(cache_mod, "_redis_client", _BrokenRedis())
    assert cache_get("analytics", ["any question"]) is None


def test_fail_open_on_redis_error_set(monkeypatch):
    monkeypatch.setattr(cache_mod, "_embed", lambda text: [1.0, 0.0, 0.0])

    class _BrokenRedis:
        def setex(self, *a, **kw): raise redis_lib.ConnectionError("down")

    monkeypatch.setattr(cache_mod, "_redis_client", _BrokenRedis())
    cache_set("analytics", ["any question"], "v", 3600)  # must not raise


def test_ttl_is_set(fake_redis):
    cache_set("chat", ["hello"], "cached_sse", 300)
    keys = fake_redis.keys("sem:chat:*")
    assert len(keys) == 1
    assert 0 < fake_redis.ttl(keys[0]) <= 300


def test_different_prefixes_do_not_collide():
    cache_set("analytics", ["same question"], "analytics_val", 3600)
    cache_set("chat", ["same question"], "chat_val", 300)
    assert cache_get("analytics", ["same question"]) == "analytics_val"
