from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.app.application.conversation_service import ConversationService
from backend.app.services.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_conversation_service_general_qa_uses_model(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    async def _fake_chat(agent_id, messages, **kwargs):
        assert agent_id == "ConversationQAAgent"
        assert messages[-1]["content"] == "你是谁"
        return {"content": "我是 RedMuse 助手。", "profile_id": "test_profile"}

    monkeypatch.setattr(service_mod.model_gateway, "chat", _fake_chat)

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    service = ConversationService(store=store)

    result = await service.handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="你是谁",
    )

    assert result["intent"].intent == "general_qa"
    assert result["assistant_message"].content == "我是 RedMuse 助手。"
    _, messages = store.get_with_messages(conversation.conversation_id)  # type: ignore[misc]
    assert [m.role for m in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_conversation_service_xhs_analysis_does_not_call_model(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    async def _fail_chat(*args, **kwargs):
        raise AssertionError("xhs_analysis should not call general QA model")

    monkeypatch.setattr(service_mod.model_gateway, "chat", _fail_chat)
    monkeypatch.setattr(
        service_mod.cookie_health_service,
        "get_cookie_health",
        lambda **kwargs: {"status": "valid", "message": "ok"},
    )

    created = {}

    class FakeTaskService:
        def create_task(self, **kwargs):
            created.update(kwargs)
            record = SimpleNamespace(
                task_id="task_conv_1",
                status=SimpleNamespace(value="pending"),
                progress=0,
            )
            return SimpleNamespace(record=record, created=True)

    class FakeEngine:
        async def start(self, task_id):
            created["started_task_id"] = task_id

    class FakeBus:
        async def publish_event(self, **kwargs):
            created.setdefault("events", []).append(kwargs)

    monkeypatch.setattr(service_mod, "task_service", FakeTaskService())
    monkeypatch.setattr(service_mod, "task_event_bus", FakeBus())
    monkeypatch.setattr(service_mod, "get_orchestration_engine", lambda: FakeEngine())

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    service = ConversationService(store=store)

    result = await service.handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="搜索小红书「巧克力」生成爆文模型",
    )

    assert result["intent"].intent == "xhs_analysis"
    assert result["assistant_message"].task_handoff.task_id == "task_conv_1"
    assert created["idempotency_key"].endswith(":xhs_analysis")
    assert created["started_task_id"] == "task_conv_1"


@pytest.mark.asyncio
async def test_conversation_task_handoff_keeps_main_and_competitor_keywords_separate(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    monkeypatch.setattr(
        service_mod.cookie_health_service,
        "get_cookie_health",
        lambda **kwargs: {"status": "valid", "message": "ok"},
    )

    created = {}

    class FakeTaskService:
        def create_task(self, **kwargs):
            created.update(kwargs)
            record = SimpleNamespace(
                task_id="task_conv_2",
                status=SimpleNamespace(value="pending"),
                progress=0,
            )
            return SimpleNamespace(record=record, created=False)

    class FakeBus:
        async def publish_event(self, **kwargs):
            raise AssertionError("idempotent hit should not publish start event")

    monkeypatch.setattr(service_mod, "task_service", FakeTaskService())
    monkeypatch.setattr(service_mod, "task_event_bus", FakeBus())

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    service = ConversationService(store=store)

    await service.handle_user_message(
        conversation_id=conversation.conversation_id,
        owner_user_id="u1",
        content="帮我搜索香水，竞品为香奈儿香水，并给出爆文模型",
        advanced_config={"note_type": "不限"},
        current_user={"user_id": "u1", "role": "user"},
    )

    assert created["keywords"] == ["香水"]
    assert created["competitor_keywords"] == ["香奈儿香水"]
    assert created["advanced_config"]["note_type"] == "不限"
    assert created["advanced_config"]["source"] == "conversation"


@pytest.mark.asyncio
async def test_conversation_service_updates_summary_after_window(monkeypatch, tmp_path):
    from backend.app.application import conversation_service as service_mod

    async def _fake_chat(agent_id, messages, **kwargs):
        return {"content": "ok", "profile_id": "test"}

    monkeypatch.setattr(service_mod.model_gateway, "chat", _fake_chat)

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    service = ConversationService(store=store)

    for idx in range(6):
        await service.handle_user_message(
            conversation_id=conversation.conversation_id,
            owner_user_id="u1",
            content=f"普通问题 {idx}",
        )

    updated = store.get(conversation.conversation_id)
    assert updated is not None
    assert "普通问题" in updated.summary
