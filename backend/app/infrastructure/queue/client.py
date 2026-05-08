"""
ARQ 客户端 helper（FastAPI 侧 enqueue 用）。

ARQ 自己维护一个连接池（`arq.connections.ArqRedis`）和 WorkerSettings 的 redis_settings 共用配置。
API 进程和 worker 进程用同一个 Redis DB 即可。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger


try:
    from arq import create_pool
    from arq.connections import ArqRedis, RedisSettings

    _ARQ_AVAILABLE = True
except ImportError:  # pragma: no cover
    create_pool = None  # type: ignore[assignment]
    ArqRedis = None  # type: ignore[misc,assignment]
    RedisSettings = None  # type: ignore[misc,assignment]
    _ARQ_AVAILABLE = False


def get_arq_redis_settings() -> "RedisSettings":
    """从 env 构造 ARQ Redis 配置。"""
    if not _ARQ_AVAILABLE:
        raise RuntimeError("arq 依赖未安装 (pip install arq)")
    host = os.getenv("REDIS_HOST", "localhost").strip() or "localhost"
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_ARQ_DB", os.getenv("REDIS_DB", "0")))
    pwd = (os.getenv("REDIS_PASSWORD") or "").strip() or None
    return RedisSettings(host=host, port=port, database=db, password=pwd)


_pool: Optional["ArqRedis"] = None


async def get_arq_pool() -> "ArqRedis":
    """拿到 ArqRedis 连接池（懒加载）。"""
    global _pool
    if not _ARQ_AVAILABLE:
        raise RuntimeError("arq 依赖未安装 (pip install arq)")
    if _pool is not None:
        return _pool
    settings = get_arq_redis_settings()
    _pool = await create_pool(settings)
    logger.info(f"[ARQ] 已连接 {settings.host}:{settings.port}/db{settings.database}")
    return _pool


async def close_arq_pool() -> None:
    """FastAPI shutdown 释放连接。"""
    global _pool
    if _pool is not None:
        try:
            await _pool.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ARQ] 关闭连接池失败: {exc}")
    _pool = None


async def enqueue_job(
    function_name: str,
    *args: Any,
    _job_id: Optional[str] = None,
    _defer_by_seconds: Optional[float] = None,
    **kwargs: Any,
) -> Optional[str]:
    """入队一个任务,返回 job_id（或 None 如果 ARQ 未就绪）。

    Args:
        function_name:    task 函数注册名（必须在 WorkerSettings.functions 里）
        args/kwargs:      任务参数
        _job_id:          自定义 job_id(ARQ 默认生成 UUID4)
        _defer_by_seconds: 延迟执行（从现在起 N 秒）
    """
    try:
        pool = await get_arq_pool()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[ARQ.enqueue_job] ARQ 未就绪,无法入队 {function_name}: {exc}")
        return None

    job = await pool.enqueue_job(
        function_name,
        *args,
        _job_id=_job_id,
        _defer_by=_defer_by_seconds,
        **kwargs,
    )
    if job is None:
        logger.warning(f"[ARQ.enqueue_job] 任务入队失败: {function_name} (可能是 job_id 重复)")
        return None
    return job.job_id


__all__ = [
    "enqueue_job",
    "get_arq_pool",
    "close_arq_pool",
    "get_arq_redis_settings",
]
