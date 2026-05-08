"""
ARQ 任务队列基础设施（阶段 4.α 引入）。

职责：
- `arq_settings.WorkerSettings`：队列 + cron 一体的 worker 配置
- `tasks/*.py`：各类具体任务（主编排、视频异步、预热、巡检、清理）
- `client.py`：FastAPI 里 enqueue 时用的 ARQ client wrapper
- `runner.py`：启动入口 `python -m backend.app.infrastructure.queue.runner`

设计决策：
- 不用 Celery/Beat（async 不友好 + event loop 嵌套）
- ARQ 内置 cron 装饰器,定时任务就是普通 async 函数 + @cron
- WorkerSettings 同一个进程承载「队列消费 + cron 触发」,运维只多一个 worker 进程
"""

from .client import enqueue_job, get_arq_pool, close_arq_pool
from .arq_settings import WorkerSettings

__all__ = [
    "WorkerSettings",
    "enqueue_job",
    "get_arq_pool",
    "close_arq_pool",
]
