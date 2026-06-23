"""RAGService 冷却熔断回归测试。

背景（修复对象）：此前 `_get_rag_service` 在初始化失败后会把
`_RAG_SERVICE_INIT_FAILED` 永久置 True —— 一次瞬时的 pgvector / 网络抖动会让
**整个进程**后续所有任务的 RAG 永久关闭，直到重启。修复改为「失败后进入
冷却期，到期自动再探测」。

本测试覆盖「用户连续交互」场景，锁住两条不可回归的不变量：
  1. 冷却期内连续调用不重复尝试构造（不高频重试拖慢任务）；
  2. 冷却期到期后，瞬时故障恢复时 RAG 必须能自动重新启用（防「永久锁存」回归）。

并在 RAGAgent 层验证：故障期任务优雅降级（占位业务规则，不崩），恢复后真实检索生效。
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.app.application.agents.base import AgentContext
from backend.app.application.agents.rag_agent import RAGAgent
from backend.app.domain.task_context import TaskContext, TaskContextWriter
from backend.app.domain.task_status import TaskStatus
from backend.app.infrastructure.repository.task_repository import TaskRecord


_FAKE_RAG_MODULE = "viral_agent.services.knowledge.rag_service"


class _Clock:
    """可控单调时钟，替换 rag_agent 模块内的 `time` 引用。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def monotonic(self) -> float:
        return self.t


class FakeBus:
    async def publish_event(self, **kwargs):
        return None


class FakeGateway:
    async def chat(self, **kwargs):
        return {"content": '{"rewritten_queries":["防晒合规"]}'}


class FakeRagService:
    def __init__(self) -> None:
        self.calls = []

    def search(
        self,
        query,
        domains=None,
        top_k=5,
        min_score=0.0,
        owner_user_id=None,
        include_all=True,
    ):
        self.calls.append({"query": query, "owner_user_id": owner_user_id})
        return []


def _install_fake_rag_module(monkeypatch, ctor) -> None:
    """把 RAGService 的来源模块注入 sys.modules。

    `_get_rag_service` 内部执行 `from viral_agent.services.knowledge.rag_service
    import RAGService`；当该叶子模块已在 sys.modules 时，import 机制直接返回它，
    不会再导入真实 viral_agent（保证测试 hermetic、不拉重依赖）。
    """
    fake_mod = types.ModuleType(_FAKE_RAG_MODULE)
    fake_mod.RAGService = ctor
    monkeypatch.setitem(sys.modules, _FAKE_RAG_MODULE, fake_mod)


def _reset_rag_singleton(monkeypatch, rag_mod, *, failed_at: float = 0.0) -> None:
    """重置进程级单例 / 失败时刻，避免受其它用例污染（teardown 自动还原）。"""
    monkeypatch.setattr(rag_mod, "_RAG_SERVICE_SINGLETON", None, raising=False)
    monkeypatch.setattr(rag_mod, "_RAG_SERVICE_INIT_FAILED_AT", failed_at, raising=False)


def test_rag_service_cooldown_recovers_after_transient_failure(monkeypatch):
    """连续调用：失败 → 冷却期内不重试 → 到期自动恢复 → 之后命中缓存。"""
    from backend.app.application.agents import rag_agent as rag_mod

    _reset_rag_singleton(monkeypatch, rag_mod)

    clock = _Clock(1000.0)
    monkeypatch.setattr(rag_mod, "time", clock)

    calls = {"count": 0, "mode": "fail"}
    sentinel = object()

    def _ctor():
        calls["count"] += 1
        if calls["mode"] == "fail":
            raise RuntimeError("pgvector 瞬时不可用")
        return sentinel

    _install_fake_rag_module(monkeypatch, _ctor)

    # ① 第一次交互：初始化失败 → 返回 None 并记录失败时刻
    assert rag_mod._get_rag_service() is None
    assert calls["count"] == 1
    assert rag_mod._RAG_SERVICE_INIT_FAILED_AT == 1000.0

    # ② 冷却期内连续交互：直接返回 None，且【不再尝试构造】
    clock.t = 1000.0 + 60       # 1 分钟后，仍在 5 分钟冷却期内
    assert rag_mod._get_rag_service() is None
    clock.t = 1000.0 + 299      # 仍 < 300s 冷却阈值
    assert rag_mod._get_rag_service() is None
    assert calls["count"] == 1  # 关键：冷却期内构造函数未被再次调用

    # ③ 冷却期到期 + 瞬时故障已恢复 → 自动重新探测成功（防「永久锁存」回归）
    calls["mode"] = "ok"
    clock.t = 1000.0 + 301      # 超过 300s 冷却
    assert rag_mod._get_rag_service() is sentinel
    assert calls["count"] == 2
    assert rag_mod._RAG_SERVICE_INIT_FAILED_AT == 0.0  # 失败时刻已清零

    # ④ 后续连续交互：命中单例缓存，不再重复构造
    assert rag_mod._get_rag_service() is sentinel
    assert rag_mod._get_rag_service() is sentinel
    assert calls["count"] == 2


def test_rag_service_steady_failure_stays_in_cooldown(monkeypatch):
    """稳态故障：冷却期内绝不重试，到期才探测一次（避免每个任务都拖慢）。"""
    from backend.app.application.agents import rag_agent as rag_mod

    _reset_rag_singleton(monkeypatch, rag_mod)
    clock = _Clock(2000.0)
    monkeypatch.setattr(rag_mod, "time", clock)

    attempts = {"count": 0}

    def _always_fail():
        attempts["count"] += 1
        raise RuntimeError("依赖缺失")

    _install_fake_rag_module(monkeypatch, _always_fail)

    assert rag_mod._get_rag_service() is None       # 第 1 次探测
    for offset in (10, 100, 299):                   # 冷却期内多次交互
        clock.t = 2000.0 + offset
        assert rag_mod._get_rag_service() is None
    assert attempts["count"] == 1                   # 冷却期内只探测过 1 次

    clock.t = 2000.0 + 301                           # 到期后再探测一次（仍失败）
    assert rag_mod._get_rag_service() is None
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_rag_agent_degrades_then_recovers_across_consecutive_runs(monkeypatch):
    """RAGAgent 连续两次任务：冷却期内优雅降级（占位、不崩）→ 到期恢复真实检索。"""
    from backend.app.application.agents import rag_agent as rag_mod

    # 预置「上一次瞬时故障」把 RAG 打入冷却期（失败时刻 = 5000）
    _reset_rag_singleton(monkeypatch, rag_mod, failed_at=5000.0)
    clock = _Clock(5000.0)
    monkeypatch.setattr(rag_mod, "time", clock)

    fake_service = FakeRagService()
    _install_fake_rag_module(monkeypatch, lambda: fake_service)

    monkeypatch.setattr(
        rag_mod.task_repository,
        "get",
        lambda task_id: TaskRecord(
            task_id=task_id, owner_user_id="owner-x", status=TaskStatus.RUNNING
        ),
    )

    def _make_ctx(task_id: str) -> TaskContext:
        ctx = TaskContext(task_id=task_id)
        TaskContextWriter(ctx).write(
            "input_spec",
            {"raw_input": "防晒合规怎么做", "parsed": {"keywords": ["防晒"]}},
            agent_id="test",
        )
        return ctx

    agent = RAGAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())

    # —— 交互 1：仍在冷却期（+60s < 300s）→ RAG 不可用 → 占位兜底，任务不崩 ——
    clock.t = 5060.0
    res1 = await agent.run(
        AgentContext(task_id="task-rag-run-1", task_context=_make_ctx("task-rag-run-1"))
    )
    assert res1.ok is True
    assert res1.output["business_rules"]      # 占位业务规则非空
    assert res1.output["vector_dim"] == 0
    assert fake_service.calls == []           # 冷却期内未触达真实检索

    # —— 交互 2：冷却期到期（+400s > 300s）→ 自动恢复，真实检索被调用 ——
    clock.t = 5400.0
    res2 = await agent.run(
        AgentContext(task_id="task-rag-run-2", task_context=_make_ctx("task-rag-run-2"))
    )
    assert res2.ok is True
    assert fake_service.calls                  # 恢复后真实 search 被调用
    assert fake_service.calls[0]["owner_user_id"] == "owner-x"
