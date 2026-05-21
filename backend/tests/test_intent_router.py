from __future__ import annotations

from backend.app.application.conversation.intent_router import IntentRouter


def test_intent_router_classifies_xhs_analysis():
    result = IntentRouter().classify(content="帮我搜索小红书「防晒」并生成爆文模型")

    assert result.intent == "xhs_analysis"
    assert result.confidence >= 0.8
    assert result.extracted_keywords == ["防晒"]



def test_intent_router_classifies_refine_when_task_active():
    result = IntentRouter().classify(content="把产品植入改得更自然", active_task_id="task_1")

    assert result.intent == "refine_canvas"
    assert "mod-viral-model-matrix" in result.target_module_ids


def test_intent_router_does_not_restart_xhs_analysis_for_active_task_followup():
    result = IntentRouter().classify(content="这个爆文模型还能怎么优化", active_task_id="task_1")

    assert result.intent == "refine_canvas"


def test_intent_router_allows_explicit_new_analysis_with_active_task():
    result = IntentRouter().classify(content="重新搜索小红书「防晒」并生成新的爆文模型", active_task_id="task_1")

    assert result.intent == "xhs_analysis"
    assert result.extracted_keywords == ["防晒"]


def test_intent_router_export_requires_task():
    result = IntentRouter().classify(content="导出 Excel")

    assert result.intent == "export"
    assert result.clarification_needed is True


def test_intent_router_splits_main_and_competitor_keywords():
    result = IntentRouter().classify(content="帮我搜索香水，竞品为香奈儿香水，并给出爆文模型")

    assert result.intent == "xhs_analysis"
    assert result.extracted_keywords == ["香水"]
    assert result.competitor_keywords == ["香奈儿香水"]
