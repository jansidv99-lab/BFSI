import json
import logging
import os
import uuid

import numpy as np
import ollama
import redis

from api.metrics import CACHE_HITS, CACHE_MISSES

logger = logging.getLogger(__name__)

_EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
_SIM_THRESHOLD = float(os.environ.get("CACHE_SIM_THRESHOLD", "0.90"))

_redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    decode_responses=True,
)


def _embed(text: str) -> list[float]:
    try:
        client = ollama.Client(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        result = client.embed(model=_EMBED_MODEL, input=text.strip().lower())
        return result.embeddings[0]
    except Exception as exc:
        logger.warning("Embedding error: %s — failing open", exc)
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def cache_get(prefix: str, parts: list[str]) -> str | None:
    query_vec = _embed("".join(parts))
    if not query_vec:
        CACHE_MISSES.labels(endpoint=prefix).inc()
        return None
    try:
        best_score, best_response = 0.0, None
        cursor = 0
        pattern = f"sem:{prefix}:*"
        while True:
            cursor, keys = _redis_client.scan(cursor, match=pattern, count=100)
            for key in keys:
                raw = _redis_client.get(key)
                if not raw:
                    continue
                entry = json.loads(raw)
                score = _cosine(query_vec, entry["embedding"])
                if score > best_score:
                    best_score, best_response = score, entry["response"]
            if cursor == 0:
                break
        if best_score >= _SIM_THRESHOLD and best_response is not None:
            CACHE_HITS.labels(endpoint=prefix).inc()
            return best_response
        CACHE_MISSES.labels(endpoint=prefix).inc()
        return None
    except redis.RedisError as exc:
        logger.warning("Redis error: %s — failing open", exc)
        return None


def cache_set(prefix: str, parts: list[str], value: str, ttl: int) -> None:
    vec = _embed("".join(parts))
    if not vec:
        return
    entry_key = f"sem:{prefix}:{uuid.uuid4()}"
    try:
        _redis_client.setex(entry_key, ttl, json.dumps({"embedding": vec, "response": value}))
    except redis.RedisError as exc:
        logger.warning("Redis error: %s — failing open", exc)
