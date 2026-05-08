"""
Cache 基础设施（阶段 4.α 引入）。

- `redis_client`：全局 async Redis 单例(懒加载 + 连接池)
- `keyword_cache`：L1 热缓存 wrapper,按 keywords tuple 哈希做 key,TTL 可配

导出顶层 helper,使用方可直接 `from backend.app.infrastructure.cache import get_redis, get_keyword_cache`。
"""

from .redis_client import (
    RedisClientSettings,
    check_redis_health,
    close_redis,
    get_redis,
    is_redis_available,
)
from .keyword_cache import KeywordCache, get_keyword_cache

__all__ = [
    "RedisClientSettings",
    "check_redis_health",
    "close_redis",
    "get_redis",
    "is_redis_available",
    "KeywordCache",
    "get_keyword_cache",
]
