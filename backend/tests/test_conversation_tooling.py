from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.application.conversation.tool_agent import ConversationToolAgent
from backend.app.application.conversation.tool_schema import ConversationToolCall
from backend.app.domain.conversation import Conversation, IntentClassification


@pytest.mark.asyncio
async def test_tool_agent_fallback_refine_asks_clarification_without_module(monkeypatch):
    from backend.app.application.conversation import tool_agent as tool_agent_mod
    from backend.app.llm.model_gateway import ModelInvocationError

    async def fail_tool_call(*args, **kwargs):
        raise ModelInvocationError("MODEL_PROVIDER_UNAVAILABLE", "no model")

    monkeypatch.setattr(tool_agent_mod.model_gateway, "chat_with_tools", fail_tool_call)
    agent = ConversationToolAgent()
    decision = await agent.decide(
        content="帮我优化一下这里",
        conversation=Conversation(conversation_id="c1", owner_user_id="u1", title="t"),
        intent=IntentClassification(intent="refine_canvas", confidence=0.8),
        recent_messages=[],
        active_task_id="task_1",
        task_status="completed",
        canvas_modules=[{"module_id": "mod-pain-points", "title": "痛点洞察"}],
    )

    assert decision.first_call is not None
    assert decision.first_call.name == "ask_clarification"
    assert decision.first_call.arguments["pending_tool_name"] == "regenerate_canvas_module"


@pytest.mark.asyncio
async def test_tool_agent_xhs_analysis_direct_when_choice_disabled(monkeypatch):
    """关闭反问开关时，xhs_analysis 直接映射到 start_xhs_analysis（旧行为保留）。"""
    from backend.app.application.conversation import tool_agent as tool_agent_mod

    monkeypatch.setattr(tool_agent_mod.settings, "agent_runtime_offer_choice", False)
    agent = ConversationToolAgent()
    decision = await agent.decide(
        content="重新搜索小红书「防晒」生成新的爆文模型",
        conversation=Conversation(conversation_id="c1", owner_user_id="u1", title="t"),
        intent=IntentClassification(intent="xhs_analysis", confidence=0.8, extracted_keywords=["防晒"]),
        recent_messages=[],
        active_task_id="task_old",
        task_status="completed",
        canvas_modules=[],
    )

    assert decision.first_call is not None
    assert decision.first_call.name == "start_xhs_analysis"
    assert decision.first_call.arguments["keywords"] == ["防晒"]


@pytest.mark.asyncio
async def test_tool_agent_xhs_analysis_offers_workflow_vs_agent_choice(monkeypatch):
    """开启反问开关时（默认），xhs_analysis 先反问『定制 workflow vs AI 自主』。"""
    from backend.app.application.conversation import tool_agent as tool_agent_mod

    monkeypatch.setattr(tool_agent_mod.settings, "agent_runtime_enabled", True)
    monkeypatch.setattr(tool_agent_mod.settings, "agent_runtime_offer_choice", True)
    agent = ConversationToolAgent()
    decision = await agent.decide(
        content="搜索小红书「防晒」生成爆文模型",
        conversation=Conversation(conversation_id="c1", owner_user_id="u1", title="t"),
        intent=IntentClassification(intent="xhs_analysis", confidence=0.9, extracted_keywords=["防晒"]),
        recent_messages=[],
        active_task_id=None,
        task_status=None,
        canvas_modules=[],
    )

    assert decision.first_call is not None
    assert decision.first_call.name == "ask_clarification"
    assert decision.first_call.arguments["pending_tool_name"] == tool_agent_mod.ANALYSIS_CHOICE_TOOL
    pending = decision.first_call.arguments["pending_arguments"]
    assert pending["workflow_tool"] == "start_xhs_analysis"
    assert pending["workflow_arguments"]["keywords"] == ["防晒"]


@pytest.mark.asyncio
async def test_tool_agent_agent_task_routes_to_run_agent_task(monkeypatch):
    from backend.app.application.conversation import tool_agent as tool_agent_mod

    monkeypatch.setattr(tool_agent_mod.settings, "agent_runtime_enabled", True)
    agent = ConversationToolAgent()
    decision = await agent.decide(
        content="检索近半年互动量1000+的Fazer笔记并逐条拆解卖点",
        conversation=Conversation(conversation_id="c1", owner_user_id="u1", title="t"),
        intent=IntentClassification(intent="agent_task", confidence=0.9, extracted_keywords=["Fazer"]),
        recent_messages=[],
        active_task_id=None,
        task_status=None,
        canvas_modules=[],
    )

    assert decision.first_call is not None
    assert decision.first_call.name == "run_agent_task"
    assert "Fazer" in decision.first_call.arguments["goal"]


def test_tool_call_from_dict_rejects_unknown_tool():
    call = ConversationToolCall.from_dict({"name": "dangerous_shell", "arguments": {"x": 1}})

    assert call.name == "answer_general"
