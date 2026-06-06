from prometheus_client import Counter

CHAT_REQUESTS = Counter(
    "chatbot_chat_requests_total",
    "Total chat requests processed (post rate-limit)",
)
ANALYTICS_REQUESTS = Counter(
    "chatbot_analytics_requests_total",
    "Total analytics requests processed (post rate-limit)",
)
RATE_LIMIT_HITS = Counter(
    "chatbot_rate_limit_hits_total",
    "Total requests rejected by rate limiter",
    ["endpoint"],
)
CACHE_HITS = Counter(
    "chatbot_cache_hits_total",
    "Cache hits by endpoint",
    ["endpoint"],
)
CACHE_MISSES = Counter(
    "chatbot_cache_misses_total",
    "Cache misses by endpoint",
    ["endpoint"],
)
