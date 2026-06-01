"""
ARQ Worker 配置（阶段 4.α）。

运行方式：
    python -m backend.app.infrastructure.queue.runner

或直接 arq CLI：
    arq backend.app.infrastructure.queue.arq_settings.WorkerSettings

WorkerSettings 同时承载：
- 队列任务（functions 列表）：用户触发 / API enqueue 的任务
- 定时任务（cron_jobs 列表）：每天/每几小时自动跑的 4 个巡检/预热任务

启动 / 关停钩子：
- on_startup：懒加载 Redis 单例、pgvector schema
- on_shutdown：释放连接池
"""

from __future__ import annotations

import os
from typing import Any, Dict

from loguru import logger


try:
    from arq import cron
    from arq.connections import RedisSettings

    _ARQ_AVAILABLE = True
except ImportError:  # pragma: no cover
    cron = None  # type: ignore[assignment]
    RedisSettings = None  # type: ignore[misc,assignment]
    _ARQ_AVAILABLE = False


def _redis_settings():
    """构造 ARQ Redis 连接配置；arq 未装时返回 None,由 Worker 入口在运行时报错。"""
    if not _ARQ_AVAILABLE or RedisSettings is None:
        return None
    host = os.getenv("REDIS_HOST", "localhost").strip() or "localhost"
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_ARQ_DB", os.getenv("REDIS_DB", "0")))
    pwd = (os.getenv("REDIS_PASSWORD") or "").strip() or None
    return RedisSettings(host=host, port=port, database=db, password=pwd)


# --------------------------------------------------------------
# Lifecycle hooks
# --------------------------------------------------------------


_STALE_JOB_IDS = ["warmup:manual"]
"""Worker 重启时需要清理的已知僵尸 job ID 列表。

这些 job 在上次进程被强杀后可能停留在 in_progress，导致下次「立即采集」
被误判为「正在执行中」而跳过入队。Worker 重启即意味着上次任务已终止。
"""


async def _clear_stale_in_progress_jobs(pool: Any) -> None:
    """清理因进程意外终止而残留的 in_progress 僵尸 job。"""
    try:
        from arq.jobs import Job, JobStatus  # noqa: PLC0415

        for job_id in _STALE_JOB_IDS:
            try:
                job = Job(job_id, pool)
                status = await job.status()
                if status == JobStatus.in_progress:
                    # 直接删除 ARQ 内部的 job key，让下次入队能正常执行
                    await pool.delete(f"arq:job:{job_id}")
                    logger.info(f"[arq.worker] 已清理僵尸 in_progress job: {job_id!r}")
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[arq.worker] 清理 job {job_id!r} 时异常（可忽略）: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.worker] 清理僵尸 job 失败: {exc}")


async def on_startup(ctx: Dict[str, Any]) -> None:
    """Worker 启动时：预热连接池 + pgvector schema + 清理僵尸 job。"""
    logger.info("[arq.worker] 启动中...")

    # Redis 单例
    try:
        from ..cache.redis_client import get_redis

        await get_redis()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.worker] Redis 未就绪: {exc}")

    # pgvector schema
    try:
        from ..storage.notes_vector_store import get_notes_vector_store

        await get_notes_vector_store().ensure_schema()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.worker] pgvector schema 未就绪: {exc}")

    # Worker 重启 = 上次所有任务已终止，清理残留的 in_progress 僵尸 job
    # 避免「立即采集」被误判为「正在执行中」而无法触发
    pool = ctx.get("redis")
    if pool is not None:
        await _clear_stale_in_progress_jobs(pool)

    logger.success("[arq.worker] 就绪")


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    """Worker 停止时：释放资源。"""
    logger.info("[arq.worker] 关停中...")
    try:
        from ..cache.redis_client import close_redis

        await close_redis()
    except Exception:
        pass
    try:
        from ..storage.db_engine import close_db_engine

        await close_db_engine()
    except Exception:
        pass


# --------------------------------------------------------------
# WorkerSettings
# --------------------------------------------------------------


def _build_functions():
    """按需导入函数（避免模块层导入引用问题）。"""
    from .tasks.run_analysis import run_analysis_task, run_video_analysis
    from .tasks.warmup import scheduled_warmup, warmup_keyword_on_demand
    from .tasks.cookie_check import cookie_health_check
    from .tasks.cleanup import cleanup_tmp
    from .tasks.hot_queries import rebuild_hot_queries

    return [
        # 队列任务（用户/API 触发）
        run_analysis_task,
        run_video_analysis,
        warmup_keyword_on_demand,
        # cron 任务（也可以手动 enqueue 触发）
        scheduled_warmup,
        cookie_health_check,
        cleanup_tmp,
        rebuild_hot_queries,
    ]


def _build_cron_jobs():
    if not _ARQ_AVAILABLE or cron is None:
        return []
    from .tasks.warmup import scheduled_warmup
    from .tasks.cookie_check import cookie_health_check
    from .tasks.cleanup import cleanup_tmp
    from .tasks.hot_queries import rebuild_hot_queries

    return [
        # 预热关键词：每小时整点触发探针。
        # 真正执行与否由函数内部决定：
        #   - 开关关闭 → skip
        #   - 距上次执行不到 `interval_hours`(用户前端配置) → skip
        # 这样用户前端改 `interval_hours` (6/12/24...) 立即生效,无需重启 worker。
        # 用户点"立即采集"时走同一函数但 force=True,穿透 interval 直接跑。
        cron(scheduled_warmup, minute={0}, run_at_startup=False),
        # Cookie 巡检：每 4 小时（系统级,不受爬虫开关影响）
        cron(cookie_health_check, hour={0, 4, 8, 12, 16, 20}, minute=15),
        # 临时文件清理：每天凌晨 3 点（系统级维护）
        cron(cleanup_tmp, hour={3}, minute=30),
        # 热词榜重建：每半小时（数据统计,与爬虫开关独立）
        cron(rebuild_hot_queries, minute={0, 30}),
    ]


class WorkerSettings:
    """ARQ WorkerSettings（入口类）。"""

    redis_settings = _redis_settings()
    functions = _build_functions()
    cron_jobs = _build_cron_jobs()
    on_startup = on_startup
    on_shutdown = on_shutdown

    # 并发控制
    max_jobs = int(os.getenv("ARQ_MAX_JOBS", "3"))  # 单 worker 最大同时跑 3 个任务
    job_timeout = int(os.getenv("ARQ_JOB_TIMEOUT", "900"))  # 单任务上限 15 分钟
    keep_result = int(os.getenv("ARQ_KEEP_RESULT", "3600"))  # 结果保留 1 小时

    # 日志
    log_results = True
