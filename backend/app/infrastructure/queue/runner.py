"""
ARQ Worker 启动入口（阶段 4.α）。

使用：

    python -m backend.app.infrastructure.queue.runner

或配合 docker-compose 的 arq-worker service 自动启动。

Windows 注意：ARQ 内部使用 asyncio,Windows 下需要 ProactorEventLoop
（playwright 也依赖）,我们在启动时显式设置一次。
"""

from __future__ import annotations

import asyncio
import os
import sys


def main() -> None:
    # Windows event loop policy
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # 让 arq CLI 找到我们的 WorkerSettings
    try:
        from arq.worker import run_worker
    except ImportError as exc:  # pragma: no cover
        print(f"[FATAL] arq 未安装: {exc}")
        sys.exit(1)

    from backend.app.infrastructure.queue.arq_settings import WorkerSettings

    print("=" * 60)
    print("RedMuse ARQ Worker 启动")
    print(f"  Redis: {WorkerSettings.redis_settings.host}:{WorkerSettings.redis_settings.port}"
          f"/db{WorkerSettings.redis_settings.database}")
    print(f"  队列任务: {len(WorkerSettings.functions)} 个")
    print(f"  cron 任务: {len(WorkerSettings.cron_jobs)} 个")
    print(f"  并发: max_jobs={WorkerSettings.max_jobs}"
          f" / timeout={WorkerSettings.job_timeout}s")
    print("=" * 60)
    print("按 Ctrl+C 停止")
    print()

    # run_worker 是阻塞调用,内部管理 event loop
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
