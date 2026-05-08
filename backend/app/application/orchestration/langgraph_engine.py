"""
LangGraphEngine：基于 LangGraph 的状态图编排（阶段 4.3pre.2 重写)。

拓扑（9 节点，Video/Image 并行,ViralModel + Sheet2Narrative）：

    START → input_parser → crawler → [image ‖ video] → viral_model
                                     → sheet2_narrative → [insight ‖ rag] → canvas → END

取消：
- 通过 execution_coordinator.run_task 启动 runner（复用并发限流 + cancel API）
- runner 内部在每个 stage 前后 check handle.is_cancelled，并在外层捕获 CancelledError

事件发布：
- agent.run 内部统一调用 emit_progress / emit_log 发布 AGENT_PROGRESS / LOG 事件
- engine 层负责 TASK_STATUS（queued/running/completed）、ERROR、DONE、CANVAS_SCHEMA_UPDATED 由 CanvasRenderAgent 发布

checkpoint：
- 4.0 使用 langgraph 内置 MemorySaver；thread_id 用 task_id
- 4.6 接 Postgres（SqlAlchemyCheckpointSaver）时替换
"""

from __future__ import annotations

import asyncio
import operator
import time
from typing import Annotated, Any, Dict, List, Optional

from loguru import logger

try:  # 可选依赖：未安装 langgraph 时由 factory 自动降级到 simple
    from langgraph.graph import END, START, StateGraph
    from langgraph.checkpoint.memory import MemorySaver

    _LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - 安装后路径覆盖
    _LANGGRAPH_AVAILABLE = False

try:
    from typing import TypedDict  # py3.8+
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore

from ...core.tracing import get_trace_id, reset_trace_id, set_trace_id
from ...domain.error_codes import ErrorCode
from ...domain.events import TaskEventType
from ...domain.task_context import task_context_store
from ...domain.task_status import TaskStatus
from ...infrastructure.db import TaskMetricRecord, metrics_store
from ...infrastructure.event_bus import task_event_bus
from ...infrastructure.execution import (
    TaskExecutionHandle,
    execution_coordinator,
)
from ...infrastructure.repository import task_repository
from ..agents import (
    AgentContext,
    CanvasRenderAgent,
    CrawlerAgent,
    ImageAnalysisAgent,
    InputParserAgent,
    InsightAgent,
    RAGAgent,
    VideoAnalysisAgent,
)
from ..agents.sheet2_narrative_agent import Sheet2NarrativeAgent
from ..agents.viral_model_agent import ViralModelAgent
from ..task_service import task_service
from .checkpoint_store import memory_checkpoint_store
from .engine import OrchestrationEngine


class _GraphState(TypedDict, total=False):
    task_id: str
    # 并发节点（insight ‖ rag）都会写 completed_stages，用 operator.add 做列表合并
    completed_stages: Annotated[List[str], operator.add]
    failed_stage: Optional[str]


class LangGraphEngine(OrchestrationEngine):
    name = "langgraph"

    def __init__(self) -> None:
        if not _LANGGRAPH_AVAILABLE:
            raise RuntimeError(
                "LangGraph 未安装。请执行 `pip install langgraph` 或设 "
                "ORCHESTRATION_ENGINE=simple。"
            )

        # 阶段 4.3pre.2: 8 节点 + Sheet2Narrative(4.3pre.6);
        # Video 改为同步并发,与 Image 并行作为多模态 fan-out 分支
        self._input_parser = InputParserAgent()
        self._crawler = CrawlerAgent()
        self._image = ImageAnalysisAgent()
        self._video = VideoAnalysisAgent()
        self._viral_model = ViralModelAgent()
        self._sheet2_narrative = Sheet2NarrativeAgent()
        self._insight = InsightAgent()
        self._rag = RAGAgent()
        self._canvas = CanvasRenderAgent()

        # 运行时上下文：task_id → AgentContext（节点函数从这里取）
        self._runtime: Dict[str, AgentContext] = {}

        self._graph = self._build_graph()

    # ----------- 节点 -----------
    def _build_graph(self):
        builder = StateGraph(_GraphState)
        builder.add_node("input_parser", self._make_node(self._input_parser, "input_parser"))
        builder.add_node("crawler", self._make_node(self._crawler, "crawler"))
        builder.add_node("image", self._make_node(self._image, "image"))
        builder.add_node("video", self._make_node(self._video, "video"))
        builder.add_node("viral_model", self._make_node(self._viral_model, "viral_model"))
        builder.add_node(
            "sheet2_narrative",
            self._make_node(self._sheet2_narrative, "sheet2_narrative"),
        )
        builder.add_node("insight", self._make_node(self._insight, "insight"))
        builder.add_node("rag", self._make_node(self._rag, "rag"))
        builder.add_node("canvas", self._make_node(self._canvas, "canvas"))

        builder.add_edge(START, "input_parser")
        builder.add_edge("input_parser", "crawler")
        # fan-out: crawler → image ‖ video (并行多模态标注)
        builder.add_edge("crawler", "image")
        builder.add_edge("crawler", "video")
        # fan-in: image + video → viral_model
        builder.add_edge("image", "viral_model")
        builder.add_edge("video", "viral_model")
        builder.add_edge("viral_model", "sheet2_narrative")
        # fan-out: sheet2_narrative → insight ‖ rag (并行)
        builder.add_edge("sheet2_narrative", "insight")
        builder.add_edge("sheet2_narrative", "rag")
        # fan-in: insight + rag → canvas
        builder.add_edge("insight", "canvas")
        builder.add_edge("rag", "canvas")
        builder.add_edge("canvas", END)

        return builder.compile(checkpointer=MemorySaver())

    def _make_node(self, agent, stage: str):
        async def _node(state: _GraphState) -> Dict[str, Any]:
            task_id = state.get("task_id") or ""
            agent_context = self._runtime.get(task_id)
            if agent_context is None:
                raise RuntimeError(f"LangGraphEngine 缺少运行时上下文：task_id={task_id}")
            handle = agent_context.handle
            if handle and handle.is_cancelled():
                raise asyncio.CancelledError(f"task {task_id} cancelled before stage {stage}")

            logger.info("[LangGraphEngine] task={} stage={} 开始", task_id, stage)
            started = time.monotonic()
            status = "ok"
            error_code: Optional[str] = None
            try:
                await agent.run(agent_context)
                logger.info("[LangGraphEngine] task={} stage={} 完成", task_id, stage)

                # checkpoint：写一次内存 store，便于 4.6 替换为 Postgres
                memory_checkpoint_store.save(
                    task_id,
                    {
                        "stage": stage,
                        "context_version": agent_context.task_context.context_version,
                    },
                )
                # 用 add-reducer：只返回本节点新增的片段；LangGraph 会自动合并
                return {"completed_stages": [stage]}
            except Exception as exc:
                status = "error"
                error_code = str(getattr(exc, "code", ErrorCode.SYSTEM_INTERNAL.value))
                raise
            finally:
                metrics_store.record_task_metric(
                    TaskMetricRecord(
                        task_id=task_id,
                        trace_id=get_trace_id(""),
                        metric_type="agent_stage",
                        agent=getattr(agent, "agent_id", stage),
                        stage=stage,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        status=status,
                        error_code=error_code,
                        metadata={"engine": self.name},
                    )
                )

        return _node

    # ----------- OrchestrationEngine 接口 -----------
    async def start(self, task_id: str) -> None:
        async def _runner(handle: TaskExecutionHandle) -> Any:
            await self._run_graph(task_id, handle)

        await execution_coordinator.run_task(task_id, _runner)

    async def cancel(self, task_id: str) -> bool:
        return await execution_coordinator.cancel(task_id)

    async def snapshot(self, task_id: str) -> Optional[Dict[str, Any]]:
        snap = memory_checkpoint_store.load(task_id)
        if snap is None:
            return None
        return {"task_id": snap.task_id, "version": snap.version, **snap.data}

    # ----------- 运行 -----------
    async def _run_graph(self, task_id: str, handle: TaskExecutionHandle) -> None:
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
            payload={"status": TaskStatus.QUEUED.value, "progress": 0, "engine": self.name, "trace_id": trace_id},
        )

        try:
            task_service.transition(task_id, TaskStatus.RUNNING)
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": TaskStatus.RUNNING.value, "progress": 3, "engine": self.name, "trace_id": trace_id},
            )

            context = task_context_store.require(task_id)
            self._runtime[task_id] = AgentContext(
                task_id=task_id, task_context=context, handle=handle
            )

            initial_state: _GraphState = {
                "task_id": task_id,
                "completed_stages": [],
                "failed_stage": None,
            }
            config = {"configurable": {"thread_id": task_id}}

            # 并发取消监听：handle.cancel_event 触发时取消 ainvoke task
            invoke_task = asyncio.create_task(
                self._graph.ainvoke(initial_state, config=config),
                name=f"langgraph:{task_id}",
            )

            async def _cancel_watcher() -> None:
                await handle.cancel_event.wait()
                if not invoke_task.done():
                    invoke_task.cancel()

            watcher = asyncio.create_task(_cancel_watcher(), name=f"lg-cancel:{task_id}")
            try:
                await invoke_task
            finally:
                watcher.cancel()
                try:
                    await watcher
                except (asyncio.CancelledError, Exception):
                    pass

            task_service.transition(task_id, TaskStatus.COMPLETED, progress=100)
            run_status = "completed"
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": TaskStatus.COMPLETED.value, "progress": 100, "engine": self.name, "trace_id": trace_id},
            )
            # 4.3pre.2: 视频已改为同步并发,DONE payload 不再带 video_pending
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.DONE,
                payload={"task_id": task_id, "engine": self.name, "trace_id": trace_id},
            )
        except asyncio.CancelledError:
            logger.warning("[LangGraphEngine] task={} cancelled", task_id)
            run_status = "cancelled"
            run_error_code = "TASK_CANCELLED"
            try:
                task_service.transition(task_id, TaskStatus.CANCELLED)
            except Exception:
                pass
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.TASK_STATUS,
                payload={"status": TaskStatus.CANCELLED.value, "engine": self.name, "trace_id": trace_id},
            )
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.DONE,
                payload={"task_id": task_id, "engine": self.name, "reason": "cancelled", "trace_id": trace_id},
            )
        except Exception as exc:
            logger.exception("[LangGraphEngine] task={} failed: {}", task_id, exc)
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
                payload={"code": str(code), "message": str(exc), "engine": self.name, "trace_id": trace_id},
            )
            await task_event_bus.publish_event(
                task_id=task_id,
                type=TaskEventType.DONE,
                payload={"task_id": task_id, "engine": self.name, "reason": "failed", "trace_id": trace_id},
            )
        finally:
            metrics_store.record_task_metric(
                TaskMetricRecord(
                    task_id=task_id,
                    trace_id=trace_id,
                    metric_type="task_run",
                    agent="LangGraphEngine",
                    stage="orchestrator",
                    duration_ms=int((time.monotonic() - run_started) * 1000),
                    status=run_status,
                    error_code=run_error_code,
                    metadata={"engine": self.name},
                )
            )
            reset_trace_id(token)
            self._runtime.pop(task_id, None)
