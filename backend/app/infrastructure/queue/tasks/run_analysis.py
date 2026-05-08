"""
主任务编排（阶段 4.α）。

本任务可由 FastAPI enqueue 入队,也可由 SimpleEngine 在 inprocess 模式下直接调用。
两个模式共用底层 `agent_orchestrator` 流转。
"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger


async def run_analysis_task(ctx: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    """ARQ 队列任务：跑完一条任务的完整 Agent 编排。

    Args:
        ctx:     ARQ worker 注入的上下文（含 redis 连接、job_id 等）
        task_id: 系统内 task_id
    """
    logger.info(f"[arq.run_analysis_task] 开始执行 task_id={task_id}")
    try:
        from ....application.orchestrator import agent_orchestrator

        await agent_orchestrator.start(task_id)
        return {"task_id": task_id, "status": "started"}
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[arq.run_analysis_task] 执行失败 task_id={task_id}: {exc}")
        return {"task_id": task_id, "status": "error", "error": str(exc)}


async def run_video_analysis(
    ctx: Dict[str, Any],
    task_id: str,
    note_id: str,
    video_url: str,
) -> Dict[str, Any]:
    """视频深度分析任务（阶段 4.2 激活）。

    目前仅骨架,4.2 会完整实现:
    - 下载视频 → 抽帧 → ASR → 多模态分析
    - 分析完成后 SSE 推送 canvas_module_updated
    """
    logger.info(f"[arq.run_video_analysis] task_id={task_id} note_id={note_id} (4.2 将激活)")
    return {
        "task_id": task_id,
        "note_id": note_id,
        "status": "stub",
        "message": "视频异步分析骨架,阶段 4.2 将激活",
    }
