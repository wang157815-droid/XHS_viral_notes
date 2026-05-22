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


def test_resolve_pending_cancel_signal_clears_pending_and_returns_selected(tmp_path):
    """用户在风险/竞品确认等待状态下说「取消」，必须清除 pending 并返回 answer_general，
    不能强制发起任务（Bug: dd841c3 引入，确认流程忽略取消信号）。"""
    from backend.app.application.conversation.tool_schema import ConversationToolCall

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    cid = conversation.conversation_id

    # 模拟 pending_tool_decision：等待风险确认
    pending_decision = {
        "tool_name": "start_xhs_analysis",
        "arguments": {
            "keywords": ["空调"],
            "risk_acknowledged": True,
            "competitor_acknowledged": True,
            "advanced_config_override": {"sample_count": "200"},
        },
        "missing_fields": ["risk_confirmation"],
    }
    store.update_conversation(cid, metadata_patch={"pending_tool_decision": pending_decision})

    service = ConversationService(store=store)
    answer_general = ConversationToolCall(
        name="answer_general",
        arguments={"question": "取消"},
        confidence=0.8,
    )

    # 用户说「取消」—— 应该清除 pending 并返回 answer_general
    result = service._resolve_pending_tool_call(
        store.get(cid).metadata,
        "取消，不做这个分析了",
        answer_general,
        conversation_id=cid,
    )

    assert result is answer_general, "取消信号应返回 selected (answer_general)，不应发起任务"
    refreshed = store.get(cid)
    assert refreshed is not None
    pending_after = (refreshed.metadata or {}).get("pending_tool_decision")
    assert pending_after is None, "取消后 pending_tool_decision 必须被清除，否则下条消息仍会误触发任务"


def test_resolve_pending_confirm_signal_proceeds_with_task(tmp_path):
    """用户在风险确认等待状态下说「好的，继续」，应该正常发起任务（确认流程的正常路径）。"""
    from backend.app.application.conversation.tool_schema import ConversationToolCall

    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conversation = store.create(owner_user_id="u1")
    cid = conversation.conversation_id

    pending_decision = {
        "tool_name": "start_xhs_analysis",
        "arguments": {
            "keywords": ["空调"],
            "risk_acknowledged": True,
            "competitor_acknowledged": True,
        },
        "missing_fields": ["risk_confirmation", "competitor_confirmation"],
    }
    store.update_conversation(cid, metadata_patch={"pending_tool_decision": pending_decision})

    service = ConversationService(store=store)
    answer_general = ConversationToolCall(
        name="answer_general",
        arguments={"question": "好的继续"},
        confidence=0.6,
    )

    result = service._resolve_pending_tool_call(
        store.get(cid).metadata,
        "好的，继续分析",
        answer_general,
        conversation_id=cid,
    )

    assert result is not None
    assert result.name == "start_xhs_analysis", "确认信号应发起 start_xhs_analysis"
    assert result.arguments.get("confirm_new_task") is True


def test_build_general_qa_messages_contains_no_fake_execution_guard(tmp_path):
    """系统提示必须包含「绝对不要假装已经执行了采集、生成或导出操作」守卫指令，
    防止 AI 错误声称任务已执行（Bug: dd841c3 意外删除此指令）。"""
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    service = ConversationService(store=store)
    msgs = service._build_general_qa_messages([{"role": "user", "content": "测试"}])
    system_content = msgs[0]["content"]
    assert "绝对不要假装已经执行了采集、生成或导出操作" in system_content, (
        "系统提示缺少关键安全指令：'绝对不要假装已经执行了采集、生成或导出操作'"
    )


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
