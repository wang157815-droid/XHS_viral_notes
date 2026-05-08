"""
全局 async Redis 客户端单例（阶段 4.α 引入）。

职责：
- 懒加载 redis.asyncio 客户端,全程共用连接池
- 连接失败时抛 `RedisUnavailable` 而不是 raw 异常,方便上层统一降级到 "纯进程内缓存/无缓存" 模式
- 提供 `check_redis_health()` 用于 cookie-health 一类状态面板
- 提供 `close_redis()` 用于 FastAPI shutdown

env 约定：
- REDIS_HOST   默认 localhost
- REDIS_PORT   默认 6379
- REDIS_DB     默认 0
- REDIS_PASSWORD 默认空

关于 ARQ：
- ARQ 自己管理一个独立的 redis 连接池(from arq.connections import RedisSettings)
- **不要** 共用这里的 client 给 ARQ,两者配置可能冲突(DB/encoding)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


try:
    from redis.asyncio import Redis as AsyncRedis
    from redis.asyncio import ConnectionPool
    from redis.exceptions import RedisError

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncRedis = None  # type: ignore[assignment]
    ConnectionPool = None  # type: ignore[assignment]
    RedisError = Exception  # type: ignore[misc,assignment]
    _REDIS_AVAILABLE = False


class RedisUnavailable(RuntimeError):
    """Redis 不可用（未安装依赖 / 连不上 / 认证失败）。"""


@dataclass(frozen=True)
class RedisClientSettings:
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    # 默认 decode_responses=True 让 get() 直接返回 str,而不是 bytes
    decode_responses: bool = True
    # 连接池大小
    max_connections: int = 20
    # 操作超时
    socket_timeout_sec: float = 5.0

    @classmethod
    def from_env(cls) -> "RedisClientSettings":
        return cls(
            host=os.getenv("REDIS_HOST", "localhost").strip() or "localhost",
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=(os.getenv("REDIS_PASSWORD") or "").strip() or None,
            decode_responses=os.getenv("REDIS_DECODE_RESPONSES", "true").lower()
            in ("1", "true", "yes"),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "20")),
            socket_timeout_sec=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5.0")),
        )


# 模块级单例（进程内共享连接池）
_client: Optional["AsyncRedis"] = None
_pool: Optional["ConnectionPool"] = None
_settings: Optional[RedisClientSettings] = None


def is_redis_available() -> bool:
    """依赖是否已安装（包 import 级别）。"""
    return _REDIS_AVAILABLE


async def get_redis(settings: Optional[RedisClientSettings] = None) -> "AsyncRedis":
    """获取全局 async Redis 客户端（懒加载）。

    首次调用会建连接池,后续调用直接返回单例。
    如果 redis 包没装 / 服务连不上,会抛 RedisUnavailable。
    """
    global _client, _pool, _settings

    if not _REDIS_AVAILABLE:
        raise RedisUnavailable("redis 依赖未安装 (pip install redis>=5.0.0)")

    if _client is not None:
        return _client

    cfg = settings or RedisClientSettings.from_env()
    _settings = cfg

    try:
        _pool = ConnectionPool(
            host=cfg.host,
            port=cfg.port,
            db=cfg.db,
            password=cfg.password,
            decode_responses=cfg.decode_responses,
            max_connections=cfg.max_connections,
            socket_timeout=cfg.socket_timeout_sec,
        )
        _client = AsyncRedis(connection_pool=_pool)
        # 主动 ping 一次,连不上快速失败（比等第一个真实调用再报错更友好）
        await _client.ping()
        logger.info(f"[RedisClient] 已连接 {cfg.host}:{cfg.port}/db{cfg.db}")
        return _client
    except RedisError as exc:  # pragma: no cover - 依赖远程服务
        _client = None
        if _pool is not None:
            try:
                await _pool.disconnect(inuse_connections=False)
            except Exception:
                pass
        _pool = None
        raise RedisUnavailable(f"Redis 连接失败 {cfg.host}:{cfg.port}: {exc}") from exc


async def close_redis() -> None:
    """FastAPI shutdown 时调用,释放连接池。"""
    global _client, _pool, _settings

    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisClient] 关闭失败: {exc}")
    if _pool is not None:
        try:
            await _pool.disconnect(inuse_connections=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RedisClient] 连接池关闭失败: {exc}")

    _client = None
    _pool = None
    _settings = None


async def check_redis_health() -> Dict[str, Any]:
    """健康检查：返回 {status, latency_ms, host} 结构,不抛异常。

    供 `/settings/system-health` 等面板查询。
    """
    if not _REDIS_AVAILABLE:
        return {"status": "unavailable", "reason": "redis_dep_missing"}

    import time

    start = time.monotonic()
    try:
        client = await get_redis()
        await client.ping()
        latency_ms = int((time.monotonic() - start) * 1000)
        cfg = _settings
        return {
            "status": "healthy",
            "latency_ms": latency_ms,
            "host": f"{cfg.host}:{cfg.port}" if cfg else "unknown",
            "db": cfg.db if cfg else None,
        }
    except RedisUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except RedisError as exc:  # pragma: no cover
        return {"status": "error", "reason": str(exc)}


__all__ = [
    "RedisClientSettings",
    "RedisUnavailable",
    "get_redis",
    "close_redis",
    "check_redis_health",
    "is_redis_available",
]
