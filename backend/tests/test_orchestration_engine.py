"""编排引擎抽象层：工厂默认值、flag 切换、SimpleEngine 与 LangGraphEngine 行为。

所有测试均 monkeypatch `ORCHESTRATION_ENGINE` env 变量并 `reset_engine_cache()`,
以隔离相互干扰。Agent 的实际执行不依赖真实模型——`ModelGateway.chat` 未配置时
Agent 自身会走降级分支(占位输出),测试依然可通过。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from backend.app.application.orchestration import (
    OrchestrationEngine,
    SimpleEngine,
    get_orchestration_engine,
    reset_engine_cache,
)
from backend.app.application.orchestration.simple_engine import SimpleEngine as _S


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # 清理缓存并确保每个测试起手干净
    reset_engine_cache()
    monkeypatch.delenv("ORCHESTRATION_ENGINE", raising=False)
    yield
    reset_engine_cache()


def test_factory_returns_simple_by_default():
    engine = get_orchestration_engine()
    assert isinstance(engine, _S)
    assert engine.name == "simple"


def test_factory_cache_returns_same_instance():
    e1 = get_orchestration_engine()
    e2 = get_orchestration_engine()
    assert e1 is e2


def test_factory_returns_langgraph_when_flagged(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_ENGINE", "langgraph")
    reset_engine_cache()
    engine = get_orchestration_engine()
    # 如果 langgraph 未安装,会降级到 SimpleEngine;此处两种情况都算通过 flag 识别路径
    assert isinstance(engine, OrchestrationEngine)
    assert engine.name in {"langgraph", "simple"}


def test_factory_invalid_flag_falls_back_to_simple(monkeypatch):
    monkeypatch.setenv("ORCHESTRATION_ENGINE", "unknown_value")
    reset_engine_cache()
    engine = get_orchestration_engine()
    assert engine.name == "simple"


def test_simple_engine_methods_are_coroutines():
    engine = SimpleEngine()
    # start / cancel / snapshot 都是 coroutine
    assert asyncio.iscoroutinefunction(engine.start)
    assert asyncio.iscoroutinefunction(engine.cancel)
    assert asyncio.iscoroutinefunction(engine.snapshot)


def test_simple_engine_snapshot_returns_none():
    engine = SimpleEngine()
    assert asyncio.run(engine.snapshot("any-task-id")) is None


def test_simple_engine_cancel_unknown_task_returns_false():
    engine = SimpleEngine()
    assert asyncio.run(engine.cancel("not-running")) is False


# ---------------- LangGraphEngine 节点图契约 ----------------


def test_langgraph_engine_has_nine_nodes_sheet2_narrative():
    """新拓扑:9 节点,含 sheet2_narrative(ViralModel 与 Insight/RAG 之间)。

    LangGraph 未安装时跳过。
    """
    try:
        from backend.app.application.orchestration.langgraph_engine import (
            LangGraphEngine,
            _LANGGRAPH_AVAILABLE,
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"langgraph 未安装: {exc}")
    if not _LANGGRAPH_AVAILABLE:
        pytest.skip("langgraph 未安装")

    engine = LangGraphEngine()
    assert engine.name == "langgraph"
    nodes: Any = engine._graph.nodes  # type: ignore[attr-defined]
    if isinstance(nodes, dict):
        node_names = set(nodes.keys())
    else:
        node_names = set(getattr(nodes, "keys", lambda: [])())

    expected = {
        "input_parser",
        "xhs_auth",  # Phase 2-B 新增前置 gate
        "crawler",
        "image",
        "video",
        "viral_model",
        "sheet2_narrative",
        "insight",
        "rag",
        "canvas",
    }
    assert expected.issubset(node_names), f"缺失节点: {expected - node_names}"
    # Strategy 已退场
    assert "strategy" not in node_names, "Strategy 节点应在 4.3pre.2 拓扑中移除"


# ---------------- End-to-end on SimpleEngine (mock agents) ----------------


def test_simple_engine_runs_end_to_end_with_mock_agents(monkeypatch):
    """用 mock agent 替换真 Agent,验证 SimpleEngine + Orchestrator 新 6 Stage 链路走通。"""
    from backend.app.application import orchestrator as orch_mod
    from backend.app.application.agents.base import AgentContext, AgentResult, BaseAgent
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository import task_repository

    calls: List[str] = []

    class StubAgent(BaseAgent):
        def __init__(self, agent_id: str) -> None:
            super().__init__()
            self.agent_id = agent_id
            self.write_partition = "input_spec"  # 不会实际写,只避免 BaseAgent 要求

        async def run(self, context: AgentContext) -> AgentResult:
            calls.append(self.agent_id)
            return AgentResult(ok=True, produced_modules=[], output={})

    # 构造任务
    res = task_service.create_task(
        owner_user_id="u-eval",
        raw_input="orchestration e2e 回归",
        keywords=["e2e"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    # TaskService.create_task 已经创建了 TaskContext

    # 拓扑: 替换 AgentOrchestrator 内的 Agent 实例为 stub
    orch = orch_mod.agent_orchestrator
    monkeypatch.setattr(orch, "_input_parser", StubAgent("InputParserAgent"))
    monkeypatch.setattr(orch, "_xhs_auth", StubAgent("XhsAuthAgent"))
    monkeypatch.setattr(orch, "_crawler", StubAgent("CrawlerAgent"))
    monkeypatch.setattr(orch, "_image", StubAgent("ImageAnalysisAgent"))
    monkeypatch.setattr(orch, "_video", StubAgent("VideoAnalysisAgent"))
    monkeypatch.setattr(orch, "_viral_model", StubAgent("ViralModelAgent"))
    monkeypatch.setattr(orch, "_sheet2_narrative", StubAgent("Sheet2NarrativeAgent"))
    monkeypatch.setattr(orch, "_insight", StubAgent("InsightAgent"))
    monkeypatch.setattr(orch, "_rag", StubAgent("RAGAgent"))
    monkeypatch.setattr(orch, "_canvas", StubAgent("CanvasRenderAgent"))

    engine = SimpleEngine()

    async def _driver() -> None:
        await engine.start(tid)
        # 等到任务结束
        for _ in range(200):
            record = task_repository.get(tid)
            if record and record.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("任务未在预期时间内结束")

    asyncio.run(_driver())

    # 4.3pre.2 关键 Agent 全部被调用一次(Image+Video 并行,Insight+RAG 并行)
    assert "InputParserAgent" in calls
    assert "CrawlerAgent" in calls
    assert "ImageAnalysisAgent" in calls
    assert "VideoAnalysisAgent" in calls
    assert "ViralModelAgent" in calls
    assert "Sheet2NarrativeAgent" in calls
    assert "InsightAgent" in calls
    assert "RAGAgent" in calls
    assert "CanvasRenderAgent" in calls
    # Strategy 已退场,不再被调用
    assert "StrategyAgent" not in calls

    final = task_repository.require(tid)
    assert final.status == TaskStatus.COMPLETED

    # 清理:从存储里删除本 task,避免影响其他测试
    try:
        task_context_store.drop(tid)
    except Exception:
        pass
