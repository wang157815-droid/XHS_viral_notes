"""4.3pre.2: AgentOrchestrator 新拓扑 stage 顺序 + Image‖Video 并行验证。"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from backend.app.application import orchestrator as orch_mod
from backend.app.application.agents.base import AgentContext, AgentResult, BaseAgent
from backend.app.application.orchestration.simple_engine import SimpleEngine
from backend.app.application.task_service import task_service
from backend.app.domain.task_context import task_context_store
from backend.app.domain.task_status import TaskStatus
from backend.app.infrastructure.repository import task_repository


class _StubAgent(BaseAgent):
    def __init__(self, agent_id: str, calls: List[str], delay: float = 0.0):
        super().__init__()
        self.agent_id = agent_id
        self.write_partition = "input_spec"
        self._calls = calls
        self._delay = delay

    async def run(self, context: AgentContext) -> AgentResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        self._calls.append(self.agent_id)
        return AgentResult(ok=True, produced_modules=[], output={})


def _drive_until_done(tid: str, engine: SimpleEngine):
    async def _drv():
        await engine.start(tid)
        for _ in range(300):
            rec = task_repository.get(tid)
            if rec and rec.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("task not done in time")

    asyncio.run(_drv())


def test_new_topology_stage_order(monkeypatch):
    """正确顺序: InputParser → Crawler → (Image,Video) → ViralModel → Sheet2Narrative → (Insight,RAG) → Canvas。

    Image/Video 顺序不固定(并行),Insight/RAG 顺序不固定。
    但 ViralModel 必须在 Image 和 Video 都完成之后;Sheet2Narrative 在 ViralModel 之后。
    """
    calls: List[str] = []
    res = task_service.create_task(
        owner_user_id="u-topology",
        raw_input="topology test",
        keywords=["k"],
        idempotency_key=None,
    )
    tid = res.record.task_id

    orch = orch_mod.agent_orchestrator
    monkeypatch.setattr(orch, "_input_parser", _StubAgent("Input", calls))
    monkeypatch.setattr(orch, "_xhs_auth", _StubAgent("XhsAuth", calls))
    monkeypatch.setattr(orch, "_crawler", _StubAgent("Crawler", calls))
    monkeypatch.setattr(orch, "_image", _StubAgent("Image", calls))
    monkeypatch.setattr(orch, "_video", _StubAgent("Video", calls))
    monkeypatch.setattr(orch, "_viral_model", _StubAgent("ViralModel", calls))
    monkeypatch.setattr(orch, "_sheet2_narrative", _StubAgent("Sheet2Narrative", calls))
    monkeypatch.setattr(orch, "_insight", _StubAgent("Insight", calls))
    monkeypatch.setattr(orch, "_rag", _StubAgent("RAG", calls))
    monkeypatch.setattr(orch, "_canvas", _StubAgent("Canvas", calls))

    _drive_until_done(tid, SimpleEngine())

    # 完成所有 stage（Phase 2-B 新增 XhsAuth）
    assert set(calls) == {
        "Input",
        "XhsAuth",
        "Crawler",
        "Image",
        "Video",
        "ViralModel",
        "Sheet2Narrative",
        "Insight",
        "RAG",
        "Canvas",
    }
    # InputParser 第一，XhsAuth 紧随其后，Crawler 之前必须经过 XhsAuth
    assert calls.index("Input") < calls.index("XhsAuth")
    assert calls.index("XhsAuth") < calls.index("Crawler")
    # Crawler 在 Image 和 Video 之前
    assert calls.index("Crawler") < calls.index("Image")
    assert calls.index("Crawler") < calls.index("Video")
    # ViralModel 在 Image 和 Video 之后
    assert calls.index("ViralModel") > calls.index("Image")
    assert calls.index("ViralModel") > calls.index("Video")
    # Sheet2Narrative 在 ViralModel 之后,Insight/RAG 之前
    assert calls.index("ViralModel") < calls.index("Sheet2Narrative")
    assert calls.index("Sheet2Narrative") < calls.index("Insight")
    assert calls.index("Sheet2Narrative") < calls.index("RAG")
    # Canvas 最后
    assert calls.index("Canvas") == len(calls) - 1

    task_context_store.drop(tid)


def test_new_topology_image_video_run_in_parallel(monkeypatch):
    """Image+Video 是 fan-out,实际并发执行: 二者总耗时 ≈ 单个最长,而非两者之和。"""
    import time

    calls: List[str] = []
    res = task_service.create_task(
        owner_user_id="u-parallel",
        raw_input="parallel test",
        keywords=["k"],
        idempotency_key=None,
    )
    tid = res.record.task_id

    orch = orch_mod.agent_orchestrator
    monkeypatch.setattr(orch, "_input_parser", _StubAgent("Input", calls))
    monkeypatch.setattr(orch, "_xhs_auth", _StubAgent("XhsAuth", calls))
    monkeypatch.setattr(orch, "_crawler", _StubAgent("Crawler", calls))
    monkeypatch.setattr(orch, "_image", _StubAgent("Image", calls, delay=0.2))
    monkeypatch.setattr(orch, "_video", _StubAgent("Video", calls, delay=0.2))
    monkeypatch.setattr(orch, "_viral_model", _StubAgent("ViralModel", calls))
    monkeypatch.setattr(orch, "_sheet2_narrative", _StubAgent("Sheet2Narrative", calls))
    monkeypatch.setattr(orch, "_insight", _StubAgent("Insight", calls))
    monkeypatch.setattr(orch, "_rag", _StubAgent("RAG", calls))
    monkeypatch.setattr(orch, "_canvas", _StubAgent("Canvas", calls))

    start = time.time()
    _drive_until_done(tid, SimpleEngine())
    elapsed = time.time() - start

    # 串行需要 ≥ 0.4s,并行 ≈ 0.2s + 微小开销 < 0.4s
    assert elapsed < 0.5, f"elapsed={elapsed:.2f}s 不像并行执行"

    task_context_store.drop(tid)
