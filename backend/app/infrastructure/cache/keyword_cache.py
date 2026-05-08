"""
L1 关键词热缓存（阶段 4.α）。

职责：
- 以 sorted keywords tuple 的 sha1 前缀作为 key
- 默认 TTL 6 小时（env `KEYWORD_CACHE_TTL_SEC` 可覆盖）
- 内容为 notes 列表的 JSON 序列化
- Redis 异常时自动降级为 miss（保障主流程不阻塞）

命中场景：用户在 6 小时内问同一组关键词（或等价）时,直接返回缓存,跳过 L2/L3,
耗时从 30-60 秒降到 < 200ms。

不命中场景：关键词变更、TTL 过期、Redis 不可用。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

from .redis_client import RedisUnavailable, get_redis, is_redis_available


_DEFAULT_TTL_SEC = int(os.getenv("KEYWORD_CACHE_TTL_SEC", "21600"))  # 6 小时
_KEY_PREFIX = os.getenv("KEYWORD_CACHE_KEY_PREFIX", "kwcache:v1:")
_MAX_NOTES_PER_ENTRY = int(os.getenv("KEYWORD_CACHE_MAX_NOTES", "200"))


class KeywordCache:
    """关键词级 L1 热缓存。

    Example:
        cache = KeywordCache()
        cached = await cache.get(["防脱精华"])
        if cached:
            return cached
        notes = await real_crawl(...)
        await cache.set(["防脱精华"], notes)
    """

    def __init__(self, ttl_sec: int = _DEFAULT_TTL_SEC, key_prefix: str = _KEY_PREFIX) -> None:
        self._ttl = ttl_sec
        self._prefix = key_prefix

    # ------------------------------------------------------------------
    # key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_keywords(keywords: List[str]) -> List[str]:
        """排序 + 去重 + 去空白,保证顺序不影响哈希结果。"""
        cleaned = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        return sorted(set(cleaned))

    def _key(self, keywords: List[str]) -> str:
        normalized = self._normalize_keywords(keywords)
        digest = hashlib.sha1("\x00".join(normalized).encode("utf-8")).hexdigest()[:20]
        return f"{self._prefix}{digest}"

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    async def get(self, keywords: List[str]) -> Optional[List[Dict[str, Any]]]:
        """读取缓存。命中返回 notes 列表,miss 或 Redis 不可用返回 None。"""
        if not keywords:
            return None

        try:
            client = await get_redis()
            raw = await client.get(self._key(keywords))
        except RedisUnavailable:
            return None
        except Exception as exc:  # noqa: BLE001 - 降级兜底不要传染上层
            logger.warning(f"[KeywordCache.get] Redis 读失败,降级 miss: {exc}")
            return None

        if not raw:
            return None

        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                notes = payload.get("notes")
                if isinstance(notes, list):
                    return notes
            elif isinstance(payload, list):
                return payload
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"[KeywordCache.get] 缓存解析失败: {exc}")
        return None

    async def set(self, keywords: List[str], notes: List[Dict[str, Any]]) -> bool:
        """写入缓存。Redis 不可用时返回 False 但不抛异常。"""
        if not keywords or not notes:
            return False

        # 限制单条缓存条目最多存多少笔记（避免大 payload 拖慢 Redis）
        truncated = notes[:_MAX_NOTES_PER_ENTRY]
        payload = {"notes": truncated, "count": len(truncated)}

        try:
            client = await get_redis()
            await client.setex(
                self._key(keywords),
                self._ttl,
                json.dumps(payload, ensure_ascii=False),
            )
            return True
        except RedisUnavailable:
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[KeywordCache.set] Redis 写失败: {exc}")
            return False

    async def invalidate(self, keywords: List[str]) -> bool:
        """主动失效指定关键词的缓存（例如用户强制刷新）。"""
        if not keywords:
            return False
        try:
            client = await get_redis()
            await client.delete(self._key(keywords))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def describe(self, keywords: List[str]) -> Dict[str, Any]:
        """诊断信息：是否命中 + TTL 剩余秒。"""
        if not keywords:
            return {"exists": False, "key": None}
        key = self._key(keywords)
        try:
            client = await get_redis()
            exists = bool(await client.exists(key))
            ttl = int(await client.ttl(key)) if exists else -2
            return {"exists": exists, "key": key, "ttl_sec": ttl}
        except Exception as exc:  # noqa: BLE001
            return {"exists": False, "key": key, "reason": str(exc)}


# 模块级单例（进程内共享）
_default_cache: Optional[KeywordCache] = None


def get_keyword_cache() -> KeywordCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = KeywordCache()
    return _default_cache


__all__ = [
    "KeywordCache",
    "get_keyword_cache",
]
