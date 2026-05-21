"""
AgentOrchestrator：阶段 4.3pre.2 新拓扑 + Sheet2Narrative(4.3pre.6)。

新链路：
  Stage 1: InputParser
  Stage 2: Crawler (四源采集)
  Stage 3: Image ‖ Video (并行 6 要素标注 → 共享 multimodal_output.annotations)
  Stage 4: ViralModel (混合聚类 → viral_model_output)
  Stage 4b: Sheet2Narrative (playbook 概括/解释/示例,可选 LLM)
  Stage 5: Insight ‖ RAG (并行)
  Stage 6: CanvasRender

变化:
- Strategy Agent 不再调用(画布的 4 个策略 module 内容为空,后续 4.3 反馈阶段再清理)
- Video 不再 fire-and-forget,改为同步并发(主流程 await 完整完成)
- DONE payload 不再带 video_pending(没有异步支路了)

对外接口保持不变。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from ..core.tracing import get_trace_id, reset_trace_id, set_trace_id
from ..domain.error_codes import ErrorCode
from ..domain.events import TaskEventType
from ..domain.task_context import task_context_store
from ..domain.task_status import TaskStatus
from ..infrastructure.db import TaskMetricRecord, metrics_store
from ..infrastructure.event_bus import task_event_bus
from ..infrastructure.execution import TaskExecutionHandle, execution_coordinator
from ..infrastructure.repository import task_repository
from .agents import (
    AgentContext,
    AgentResult,
    BaseAgent,
    CanvasRenderAgent,
    CrawlerAgent,
    ImageAnalysisAgent,
    InputParserAgent,
    InsightAgent,
    RAGAgent,
    VideoAnalysisAgent,
    XhsAuthAgent,
)
from .agents.sheet2_narrative_agent import Sheet2NarrativeAgent
from .agents.viral_model_agent import ViralModelAgent
from .task_service import task_service


@dataclass
class OrchestratorRunSummary:
    task_id: str
    ok: bool
    failed_stage: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class AgentOrchestrator:
    def __init__(self) -> None:
        self._input_parser = InputParserAgent()
        self._xhs_auth = XhsAuthAgent()
        self._crawler = CrawlerAgent()
        self._image = ImageAnalysisAgent()
        self._video = VideoAnalysisAgent()
        self._viral_model = ViralModelAgent()
        self._sheet2_narrative = Sheet2NarrativeAgent()
        self._insight = InsightAgent()
        self._rag = RAGAgent()
        self._canvas = CanvasRenderAgent()

    async def start(self, task_id: str) -> None:
        """异步启动：不阻塞 API 响应。"""

        async def _runner(handle: TaskExecutionHandle):
            await self._run(task_id, handle)

        await execution_coordinator.run_task(task_id, _runner)

    async def _run(self, task_id: str, handle: TaskExecutionHandle) -> OrchestratorRunSummary:
        record = task_repository.require(task_id)
        trace_id = str((record.input_spec or {}).get("_trace_id") or "")
        token = set_trace_id(trace_id)
        trace_id = get_trace_id()
        run_started = time.monotonic()
        run_status = "running"
        run_error_code: Optional[str] = None
        task_service.transition(task_id, TaskStatus.QUEUED)
        await task_event_bus.publish_event(
            task_id=task_id,
            type=TaskEventType.TASK_STATUS,
            payload={"status": TaskStatus.QUEUED.value, "progress": 0, "trace_id": trace_id},
        )

        try:
            task_service.transition(task_id, TaskStatus.RUNNING)
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": TaskStatus.RUNNING.value, "progress": 3, "trace_id": trace_id},
            )

            context = task_context_store.require(task_id)
            agent_context = AgentContext(task_id=task_id, task_context=context, handle=handle)

            # Stage 1: InputParser
            await self._run_stage(agent_context, self._input_parser, stage="input_parser")
            handle.raise_if_cancelled()

            # Stage 1b: XhsAuth (Phase 2-B 前置 gate；未授权 → AUTH_XHS_NOT_BOUND)
            await self._run_stage(agent_context, self._xhs_auth, stage="xhs_auth")
            handle.raise_if_cancelled()

            # Stage 2: Crawler (四源采集)
            await self._run_stage(agent_context, self._crawler, stage="crawler")
            handle.raise_if_cancelled()

            # Stage 3: Image ‖ Video (并行 6 要素标注,共享 multimodal_output.annotations)
            # 读取 InputParser 输出的 pipeline_config 动态裁剪不需要的 Agent
            parsed_cfg = (
                (context.get("input_spec") or {}).get("parsed") or {}
            ).get("pipeline_config") or {}
            run_video = bool(parsed_cfg.get("run_video_analysis", True))
            run_rag = bool(parsed_cfg.get("run_rag", True))

            multimodal_branches = {
                "image": lambda: self._run_stage(
                    agent_context, self._image, stage="image_analysis"
                ),
            }
            if run_video:
                multimodal_branches["video"] = lambda: self._run_stage(
                    agent_context, self._video, stage="video_analysis"
                )
            else:
                await task_event_bus.publish_event(
                    task_id=task_id,
                    type=TaskEventType.TASK_STATUS,
                    payload={"status": "running", "stage": "video_analysis", "skipped": True, "reason": "pipeline_config.run_video_analysis=false"},
                )
                logger.info("[Orchestrator] task={} stage=video_analysis 已跳过（pipeline_config）", task_id)
            mm_results = await execution_coordinator.run_branches(
                handle, multimodal_branches
            )
            for name, result in mm_results.items():
                if not result.ok:
                    err = result.error
                    raise RuntimeError(f"多模态分支 {name} 失败：{err}")
            handle.raise_if_cancelled()

            # Stage 4: ViralModel (混合聚类生成爆文模型矩阵)
            await self._run_stage(agent_context, self._viral_model, stage="viral_model")
            handle.raise_if_cancelled()

            # Stage 4b: Sheet2 playbook 叙事(LLM 可选补全)
            await self._run_stage(
                agent_context, self._sheet2_narrative, stage="sheet2_narrative"
            )
            handle.raise_if_cancelled()

            # Stage 5: Insight ‖ RAG (并行)
            branches: Dict[str, Any] = {
                "insight": lambda: self._run_stage(agent_context, self._insight, stage="insight"),
            }
            if run_rag:
                branches["rag"] = lambda: self._run_stage(agent_context, self._rag, stage="rag")
            else:
                await task_event_bus.publish_event(
                    task_id=task_id,
                    type=TaskEventType.TASK_STATUS,
                    payload={"status": "running", "stage": "rag", "skipped": True, "reason": "pipeline_config.run_rag=false"},
                )
                logger.info("[Orchestrator] task={} stage=rag 已跳过（pipeline_config）", task_id)
            branch_results = await execution_coordinator.run_branches(handle, branches)
            for name, result in branch_results.items():
                if not result.ok:
                    err = result.error
                    raise RuntimeError(f"分支 {name} 失败：{err}")
            handle.raise_if_cancelled()

            # Stage 6: CanvasRender
            await self._run_stage(agent_context, self._canvas, stage="canvas")

            task_service.transition(task_id, TaskStatus.COMPLETED, progress=100)
            run_status = "completed"
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": TaskStatus.COMPLETED.value, "progress": 100, "trace_id": trace_id},
            )
            # 4.3pre.2: 视频已改为同步并发,DONE payload 不再带 video_pending
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.DONE,
                payload={"task_id": task_id, "trace_id": trace_id},
            )
            return OrchestratorRunSummary(task_id=task_id, ok=True)
        except Exception as exc:
            logger.exception("[Orchestrator] task={} 执行失败: {}", task_id, exc)
            code = getattr(exc, "code", ErrorCode.SYSTEM_INTERNAL.value)
            run_status = "failed"
            run_error_code = str(code)
            try:
                task_service.transition(
                    task_id,
                    TaskStatus.FAILED,
                    error_code=str(code),
                    last_error=str(exc),
                )
            except Exception:
                pass
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.ERROR,
                payload={"code": str(code), "message": str(exc), "trace_id": trace_id},
            )
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.DONE,
                payload={"task_id": task_id, "reason": "failed", "trace_id": trace_id},
            )
            return OrchestratorRunSummary(
                task_id=task_id,
                ok=False,
                error_code=str(code),
                error_message=str(exc),
            )
        finally:
            metrics_store.record_task_metric(
                TaskMetricRecord(
                    task_id=task_id,
                    trace_id=trace_id,
                    metric_type="task_run",
                    stage="orchestrator",
                    agent="AgentOrchestrator",
                    duration_ms=int((time.monotonic() - run_started) * 1000),
                    status=run_status,
                    error_code=run_error_code,
                )
            )
            reset_trace_id(token)

    async def _run_stage(
        self,
        agent_context: AgentContext,
        agent: BaseAgent,
        *,
        stage: str,
    ) -> AgentResult:
        logger.info(
            "[AgentOrchestrator] task={} stage={}({}) 开始",
            agent_context.task_id,
            stage,
            agent.agent_id,
        )
        started = time.monotonic()
        status = "ok"
        error_code: Optional[str] = None
        try:
            result = await agent.run(agent_context)
            logger.info(
                "[AgentOrchestrator] task={} stage={}({}) 完成",
                agent_context.task_id,
                stage,
                agent.agent_id,
            )
            # 通知前端该 agent 已完成，前端据此将步骤图标切绿、自动收回
            await task_event_bus.publish_event(
                task_id=agent_context.task_id,
                type=TaskEventType.AGENT_PROGRESS,
                payload={"agent_id": agent.agent_id, "message": "", "done": True},
            )
            ctx = agent_context.task_context
            task_repository.bump_context_version(agent_context.task_id, ctx.context_version)
            return result
        except Exception as exc:
            status = "error"
            error_code = str(getattr(exc, "code", ErrorCode.SYSTEM_INTERNAL.value))
            raise
        finally:
            metrics_store.record_task_metric(
                TaskMetricRecord(
                    task_id=agent_context.task_id,
                    trace_id=get_trace_id(""),
                    metric_type="agent_stage",
                    agent=agent.agent_id,
                    stage=stage,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status=status,
                    error_code=error_code,
                )
            )


agent_orchestrator = AgentOrchestrator()
