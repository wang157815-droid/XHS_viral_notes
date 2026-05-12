from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.app.application.conversation_service import ConversationService
from backend.app.application.conversation.tool_schema import ConversationToolCall, ConversationToolDecision
from backend.app.domain.canvas.schema import CanvasModule, CanvasSchema
from backend.app.domain.module_status import ModuleStatus
from backend.app.services.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_task_handoff_updates_active_task(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    monkeypatch.setattr(
        service_mod.cookie_health_service,
        "get_cookie_health",
        lambda **kwargs: {"status": "valid", "message": "ok"},
    )

    class FakeTaskService:
        def create_task(self, **kwargs):
            return SimpleNamespace(
                record=SimpleNamespace(
                    task_id="task_44",
                    status=SimpleNamespace(value="pending"),
                    progress=0,
                ),
                created=True,
            )

    class FakeEngine:
        async def start(self, task_id):
            assert task_id == "task_44"

    class FakeBus:
        async def publish_event(self, **kwargs):
            assert kwargs["task_id"] == "task_44"

    monkeypatch.setattr(service_mod, "task_service", FakeTaskService())
    monkeypatch.setattr(service_mod, "get_orchestration_engine", lambda: FakeEngine())
    monkeypatch.setattr(service_mod, "task_event_bus", FakeBus())

    async def fake_decide(**kwargs):
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="start_xhs_analysis",
                    arguments={"keywords": ["防晒"], "confirm_new_task": True},
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(service_mod.conversation_tool_agent, "decide", fake_decide)

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    result = await ConversationService(store=store).handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="搜索小红书「防晒」生成爆文模型",
        current_user={"user_id": "u1", "role": "user"},
    )

    assert result["assistant_message"].task_handoff.task_id == "task_44"
    assert store.get(conversation.conversation_id).active_task_id == "task_44"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_task_handoff_cookie_expired_does_not_create_task(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    monkeypatch.setattr(
        service_mod.cookie_health_service,
        "get_cookie_health",
        lambda **kwargs: {"status": "expired", "message": "expired"},
    )

    class FakeTaskService:
        def create_task(self, **kwargs):
            raise AssertionError("expired cookie should not create task")

    monkeypatch.setattr(service_mod, "task_service", FakeTaskService())

    async def fake_decide(**kwargs):
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="start_xhs_analysis",
                    arguments={"keywords": ["防晒"], "confirm_new_task": True},
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(service_mod.conversation_tool_agent, "decide", fake_decide)

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    result = await ConversationService(store=store).handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="搜索小红书「防晒」生成爆文模型",
        current_user={"user_id": "u1", "role": "user"},
    )

    assert "Cookie 已过期" in result["assistant_message"].content
    assert result["assistant_message"].task_handoff is None


@pytest.mark.asyncio
async def test_refine_canvas_triggers_local_module_regeneration(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    canvas = CanvasSchema(
        task_id="task_44",
        title="测试画布",
        modules=[
            CanvasModule(
                module_id="mod-pain-points",
                title="痛点洞察",
                layer=2,
                status=ModuleStatus.READY,
                version=1,
            )
        ],
    )
    calls = []

    class FakeTaskService:
        def create_task(self, **kwargs):
            raise AssertionError("refine_canvas should not create a new analysis task")

        def get_canvas(self, task_id):
            assert task_id == "task_44"
            return canvas

        def set_canvas(self, task_id, next_canvas):
            assert task_id == "task_44"
            assert next_canvas is canvas
            return next_canvas

    class FakeBus:
        async def publish_event(self, **kwargs):
            calls.append(("event", kwargs))

    async def fake_run_module_regeneration(**kwargs):
        calls.append(("regen", kwargs))

    monkeypatch.setattr(service_mod, "task_service", FakeTaskService())
    monkeypatch.setattr(service_mod, "task_event_bus", FakeBus())
    monkeypatch.setattr(service_mod, "run_module_regeneration", fake_run_module_regeneration)

    async def fake_decide(**kwargs):
        return ConversationToolDecision(
            calls=[
                ConversationToolCall(
                    name="regenerate_canvas_module",
                    arguments={
                        "task_id": "task_44",
                        "module_id": "mod-pain-points",
                        "instruction": "优化痛点洞察，让表达更自然",
                    },
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(service_mod.conversation_tool_agent, "decide", fake_decide)

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    result = await ConversationService(store=store).handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="优化痛点洞察，让表达更自然",
        active_task_id="task_44",
        current_user={"user_id": "u1", "role": "user"},
    )
    await asyncio.sleep(0)

    assistant = result["assistant_message"]
    assert assistant.intent == "refine_canvas"
    assert assistant.task_handoff is None
    assert assistant.debug["regeneration_started"] is True
    assert assistant.debug["module_id"] == "mod-pain-points"
    assert canvas.modules[0].status == ModuleStatus.GENERATING
    assert canvas.modules[0].version == 2
    assert any(kind == "regen" and payload["module_id"] == "mod-pain-points" for kind, payload in calls)
