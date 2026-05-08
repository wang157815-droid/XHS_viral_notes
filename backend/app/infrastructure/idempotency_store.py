"""Idempotency stores.

Phase 4.6 runtime uses Redis so idempotency works across workers. The original
in-memory store remains as a lightweight unit-test fake.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Optional

from .cache.redis_client import get_redis


@dataclass
class IdempotencyRecord:
    key: str
    created_at: float
    completed: bool = False
    status_code: int = 0
    body: Optional[Dict[str, Any]] = None


@dataclass
class IdempotencyStore:
    ttl_seconds: int = 600
    _records: Dict[str, IdempotencyRecord] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def _purge_expired(self) -> None:
        now = time.time()
        expired_keys = [k for k, r in self._records.items() if (now - r.created_at) > self.ttl_seconds]
        for k in expired_keys:
            self._records.pop(k, None)

    def begin(self, key: str) -> bool:
        with self._lock:
            self._purge_expired()
            if key in self._records:
                return False
            self._records[key] = IdempotencyRecord(key=key, created_at=time.time())
            return True

    def complete(self, key: str, status_code: int, body: Dict[str, Any]) -> None:
        with self._lock:
            record = self._records.get(key)
            if not record:
                self._records[key] = IdempotencyRecord(
                    key=key,
                    created_at=time.time(),
                    completed=True,
                    status_code=status_code,
                    body=body,
                )
                return
            record.completed = True
            record.status_code = status_code
            record.body = body

    def abort(self, key: str) -> None:
        """首次写入失败时移除占位，允许客户端重试。"""
        with self._lock:
            self._records.pop(key, None)

    def lookup(self, key: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            self._purge_expired()
            return self._records.get(key)


class RedisIdempotencyStore:
    ttl_seconds: int = 86400

    _BEGIN_SCRIPT = """
    if redis.call('EXISTS', KEYS[1]) == 1 then
        return 0
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    return 1
    """

    async def begin(self, key: str) -> bool:
        client = await get_redis()
        payload = json.dumps(
            {"key": key, "created_at": time.time(), "completed": False},
            ensure_ascii=False,
        )
        result = await client.eval(self._BEGIN_SCRIPT, 1, self._redis_key(key), payload, self.ttl_seconds)
        return bool(int(result or 0))

    async def complete(self, key: str, status_code: int, body: Dict[str, Any]) -> None:
        client = await get_redis()
        existing = await self.lookup(key)
        created_at = existing.created_at if existing else time.time()
        payload = json.dumps(
            {
                "key": key,
                "created_at": created_at,
                "completed": True,
                "status_code": int(status_code),
                "body": body,
            },
            ensure_ascii=False,
        )
        await client.set(self._redis_key(key), payload, ex=self.ttl_seconds)

    async def abort(self, key: str) -> None:
        client = await get_redis()
        await client.delete(self._redis_key(key))

    async def lookup(self, key: str) -> Optional[IdempotencyRecord]:
        client = await get_redis()
        raw = await client.get(self._redis_key(key))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return IdempotencyRecord(
            key=str(data.get("key") or key),
            created_at=float(data.get("created_at") or time.time()),
            completed=bool(data.get("completed")),
            status_code=int(data.get("status_code") or 0),
            body=data.get("body"),
        )

    @staticmethod
    def _redis_key(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"idem:{digest}"


idempotency_store = RedisIdempotencyStore()
