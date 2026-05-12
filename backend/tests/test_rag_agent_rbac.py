from __future__ import annotations

import pytest

from backend.app.application.agents.base import AgentContext
from backend.app.application.agents.rag_agent import RAGAgent
from backend.app.domain.task_context import TaskContext, TaskContextWriter
from backend.app.domain.task_status import TaskStatus
from backend.app.infrastructure.repository.task_repository import TaskRecord


class FakeBus:
    async def publish_event(self, **kwargs):
        return None


class FakeGateway:
    async def chat(self, **kwargs):
        return {"content": '{"rewritten_queries":["防晒合规"]}'}


class FakeRagService:
    def __init__(self):
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
        self.calls.append(
            {
                "query": query,
                "owner_user_id": owner_user_id,
                "include_all": include_all,
            }
        )
        return []


@pytest.mark.asyncio
async def test_rag_agent_filters_by_task_owner(monkeypatch):
    from backend.app.application.agents import rag_agent as rag_mod

    fake_service = FakeRagService()
    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: fake_service)
    monkeypatch.setattr(
        rag_mod.task_repository,
        "get",
        lambda task_id: TaskRecord(
            task_id=task_id,
            owner_user_id="owner-1",
            status=TaskStatus.RUNNING,
        ),
    )

    ctx = TaskContext(task_id="task-rag-rbac")
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "raw_input": "防晒合规怎么做",
            "parsed": {"keywords": ["防晒"]},
        },
        agent_id="test",
    )

    agent = RAGAgent(model_gateway_instance=FakeGateway(), event_bus=FakeBus())
    result = await agent.run(AgentContext(task_id=ctx.task_id, task_context=ctx))

    assert result.ok is True
    assert fake_service.calls
    assert fake_service.calls[0]["owner_user_id"] == "owner-1"
    assert fake_service.calls[0]["include_all"] is False
