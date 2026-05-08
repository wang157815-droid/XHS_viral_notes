"""
SimpleEngine：封装自研 AgentOrchestrator（阶段4.0 默认引擎 / 阶段 4.α 扩展）。

职责：
- `start(task_id)` → 按 env TASK_RUNNER 决定：
    - inprocess (默认): `agent_orchestrator.start(task_id)` 进程内 asyncio.create_task
    - arq:               enqueue 到 ARQ 队列让 worker 执行（跨进程）
- `cancel(task_id)`  → inprocess 模式走 execution_coordinator,arq 模式走 abort_job
- `snapshot(task_id)` → 阶段4.0 暂不返回（4.6 Postgres checkpoint 接入后补）

env 开关:
    TASK_RUNNER=inprocess   # 默认,兼容老流程
    TASK_RUNNER=arq         # 切到 ARQ 队列(需 worker 进程在跑)

灰度策略：本地/小流量维持 inprocess；生产环境稳定后切 arq 以获得水平扩展能力。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from loguru import logger

from ...infrastructure.execution import execution_coordinator
from ..orchestrator import agent_orchestrator
from .engine import OrchestrationEngine


def _get_runner_mode() -> str:
    mode = (os.getenv("TASK_RUNNER") or "inprocess").strip().lower()
    return mode if mode in ("inprocess", "arq") else "inprocess"


class SimpleEngine(OrchestrationEngine):
    name = "simple"

    async def start(self, task_id: str) -> None:
        mode = _get_runner_mode()
        if mode == "arq":
            ok = await self._start_via_arq(task_id)
            if ok:
                return
            # ARQ 入队失败降级为进程内（至少任务不会卡住）
            logger.warning(
                f"[SimpleEngine.start] ARQ 入队失败,降级进程内执行 task_id={task_id}"
            )
        await agent_orchestrator.start(task_id)

    async def cancel(self, task_id: str) -> bool:
        # 两个路径都尝试：
        # 1. 进程内: execution_coordinator 置 cancel_event
        # 2. ARQ 模式: abort_job
        hit_inprocess = await execution_coordinator.cancel(task_id)

        if _get_runner_mode() == "arq":
            try:
                from ...infrastructure.queue.client import get_arq_pool

                pool = await get_arq_pool()
                await pool.abort_job(f"task:{task_id}")
                logger.info(f"[SimpleEngine.cancel] ARQ abort_job task:{task_id}")
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[SimpleEngine.cancel] ARQ abort 失败: {exc}")

        return hit_inprocess

    async def snapshot(self, task_id: str) -> Optional[Dict[str, Any]]:
        return None

    # ------------------------------------------------------------------
    # private
    # ------------------------------------------------------------------

    async def _start_via_arq(self, task_id: str) -> bool:
        try:
            from ...infrastructure.queue.client import enqueue_job

            job_id = await enqueue_job(
                "run_analysis_task",
                task_id,
                _job_id=f"task:{task_id}",  # 和 task_id 绑定,便于 abort
            )
            if job_id:
                logger.info(f"[SimpleEngine.start] ARQ 入队成功 job_id={job_id}")
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[SimpleEngine.start] ARQ 入队异常: {exc}")
        return False
