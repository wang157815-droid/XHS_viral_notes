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


async def enqueue_job_replace_queued(
    function_name: str,
    *args: Any,
    _job_id: Optional[str] = None,
    _defer_by_seconds: Optional[float] = None,
    **kwargs: Any,
) -> tuple[Optional[str], str]:
    """入队任务，手动触发专用——最高优先级语义。

    - 若同名 job 仍在排队（queued/deferred）：先从队列中删除，再重新入队
    - 若同名 job 正在执行（in_progress）：返回 (None, 'in_progress')，不重复入队
    - 否则正常入队

    Returns:
        (job_id_or_None, status) —— status: 'enqueued' | 'in_progress' | 'failed'
    """
    try:
        pool = await get_arq_pool()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[ARQ.enqueue_job_replace_queued] ARQ 未就绪: {exc}")
        return None, "failed"

    if _job_id:
        try:
            from arq.jobs import Job, JobStatus  # noqa: PLC0415

            existing = Job(_job_id, pool)
            status = await existing.status()
            if status in (JobStatus.queued, JobStatus.deferred):
                # 还在排队，踢掉旧的，让本次手动触发优先
                await pool.zrem(b"arq:queue", _job_id)
                logger.info(f"[ARQ] 已移除排队中的旧任务 '{_job_id}'，重新入队以保证手动触发优先")
            elif status == JobStatus.in_progress:
                logger.info(f"[ARQ] 任务 '{_job_id}' 正在执行中，跳过重复入队")
                return None, "in_progress"
            # complete / not_found：直接走下面正常入队
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ARQ] 检查任务状态失败，将直接尝试入队: {exc}")

    job = await pool.enqueue_job(
        function_name,
        *args,
        _job_id=_job_id,
        _defer_by=_defer_by_seconds,
        **kwargs,
    )
    if job is None:
        logger.warning(f"[ARQ.enqueue_job_replace_queued] 入队失败: {function_name}")
        return None, "failed"
    return job.job_id, "enqueued"


__all__ = [
    "enqueue_job",
    "enqueue_job_replace_queued",
    "get_arq_pool",
    "close_arq_pool",
    "get_arq_redis_settings",
]
