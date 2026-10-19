"""M2（设计 v1.1 §G.M2）：读失败语义 + 观测底座。"""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from backend.app.application.agent_runtime.tool_registry import (
    OBS_MAX_CHARS,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from backend.app.application.agent_runtime.tools import knowledge as knowledge_tool
from backend.app.domain.context import BlockKind, ContextBlock, ContextManifest, Trust
from backend.tests.test_agents_real import FakeBus, FakeGateway, _make_context


class _FailingRag:
    def search(self, *args, **kwargs):
        from viral_agent.services.knowledge.rag_service import RetrievalUnavailableError

        raise RetrievalUnavailableError("embedding down")


class _EmptyRag:
    def search(self, *args, **kwargs):
        return []


# ── 1. search_knowledge：失败 ≠ 无命中（场景 3c）────────────────────────────


@pytest.mark.asyncio
async def test_search_knowledge_failure_returns_retrieval_unavailable(monkeypatch):
    monkeypatch.setattr(knowledge_tool, "_get_rag", lambda: _FailingRag())
    res = await knowledge_tool._search_knowledge({"query": "防晒"}, ToolContext(owner_user_id="u1"))
    assert res.ok is False
    assert res.error == "RETRIEVAL_UNAVAILABLE"
    assert "未检索到" not in res.content


@pytest.mark.asyncio
async def test_search_knowledge_init_failure_is_also_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("pgvector unreachable")

    monkeypatch.setattr(knowledge_tool, "_get_rag", _boom)
    res = await knowledge_tool._search_knowledge({"query": "防晒"}, ToolContext(owner_user_id="u1"))
    assert res.ok is False and res.error == "RETRIEVAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_search_knowledge_empty_still_reports_no_hit(monkeypatch):
    monkeypatch.setattr(knowledge_tool, "_get_rag", lambda: _EmptyRag())
    res = await knowledge_tool._search_knowledge({"query": "防晒"}, ToolContext(owner_user_id="u1"))
    assert res.ok is True
    assert "未检索到" in res.content


@pytest.mark.asyncio
async def test_search_knowledge_flag_off_keeps_old_behavior(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_FAILURE_EXPLICIT", "false")
    monkeypatch.setattr(knowledge_tool, "_get_rag", lambda: _FailingRag())
    with pytest.raises(Exception):
        await knowledge_tool._search_knowledge({"query": "防晒"}, ToolContext(owner_user_id="u1"))


# ── 2. RAGService.search 向调用方传递失败 ──────────────────────────────────


def _bare_rag_service():
    from viral_agent.services.knowledge.rag_service import RAGService

    svc = object.__new__(RAGService)
    svc.embedding_model = "test-model"

    def _fail(_texts):
        raise RuntimeError("embedding 503")

    svc._get_embeddings = _fail
    return svc


def test_rag_service_search_raises_on_failure():
    from viral_agent.services.knowledge.rag_service import RetrievalUnavailableError

    with pytest.raises(RetrievalUnavailableError) as ei:
        _bare_rag_service().search("q", owner_user_id="u1")
    assert ei.value.code == "RETRIEVAL_UNAVAILABLE"


def test_rag_service_search_flag_off_returns_empty(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_FAILURE_EXPLICIT", "false")
    assert _bare_rag_service().search("q", owner_user_id="u1") == []


# ── 3. RAGAgent：兜底行为不变，但失败可区分 ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rag, expected", [(_FailingRag(), "unavailable"), (_EmptyRag(), "empty")])
async def test_rag_agent_distinguishes_failure_from_empty(monkeypatch, rag, expected):
    from backend.app.application.agents import rag_agent as rag_mod

    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: rag)
    agent = rag_mod.RAGAgent(
        model_gateway_instance=FakeGateway(['{"rewritten_queries": ["q1", "q2"]}']),
        event_bus=FakeBus(),
    )
    ctx, ac = _make_context({"raw_input": "x", "keywords": ["k"]})
    await agent.run(ac)
    out = ctx.get("rag_output")
    assert out["retrieval_status"] == expected
    # 兜底规则不变
    assert "rule-compliance-01" in {r["rule_id"] for r in out["business_rules"]}


@pytest.mark.asyncio
async def test_rag_agent_service_none_is_unavailable(monkeypatch):
    from backend.app.application.agents import rag_agent as rag_mod

    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: None)
    agent = rag_mod.RAGAgent(
        model_gateway_instance=FakeGateway(['{"rewritten_queries": ["q"]}']), event_bus=FakeBus()
    )
    ctx, ac = _make_context({"raw_input": "x", "keywords": ["k"]})
    await agent.run(ac)
    assert ctx.get("rag_output")["retrieval_status"] == "unavailable"


# ── 4. KnowledgeQA ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rag, expected", [(_FailingRag(), "unavailable"), (_EmptyRag(), "empty")])
async def test_knowledge_qa_distinguishes_failure_from_empty(rag, expected):
    from backend.app.application.conversation.knowledge_qa_service import KnowledgeQAService
    from backend.app.domain.conversation import IntentClassification

    svc = KnowledgeQAService(rag_factory=lambda: rag)
    intent = IntentClassification(intent="knowledge_qa", confidence=0.9)
    _, citations, debug = await svc.answer(question="防晒", intent=intent, current_user={"user_id": "u1"})
    assert citations == []
    assert debug["retrieval_status"] == expected
    if expected == "unavailable":
        assert debug["model_error_code"] == "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"


# ── 5. domain/context ─────────────────────────────────────────────────────


def test_manifest_fields_and_no_memory_values():
    m = ContextManifest(
        caller="planner",
        conversation_id="c1",
        iteration=4,
        budget=28000,
        blocks=[
            ContextBlock(BlockKind.SYSTEM, 1290, trust=Trust.INSTRUCTION),
            ContextBlock(BlockKind.MEMORY, 380, trust=Trust.USER_DATA, status="ok"),
            ContextBlock(BlockKind.OBSERVATIONS, 900, trust=Trust.UNTRUSTED, derived=True, truncated=False),
        ],
        memory_hits=[{"id": "m_12", "kind": "profile", "slot": "competitors", "content": "竞品是 A", "value": ["A"]}],
        prompt_versions={"planner": "a91c03e2b7f1"},
    )
    d = m.to_dict()
    for key in ("caller", "conversation_id", "iteration", "budget", "total_est", "blocks",
                "memory_hits", "compression", "trimmed", "prompt_versions"):
        assert key in d
    assert d["total_est"] == 1290 + 380 + 900
    assert d["blocks"][2] == {"kind": "observations", "tokens": 900, "trust": "untrusted",
                              "derived": True, "truncated": False}
    assert d["memory_hits"] == [{"id": "m_12", "kind": "profile", "slot": "competitors"}]
    assert "竞品是 A" not in json.dumps(d, ensure_ascii=False)


# ── 6. ModelGateway：metadata.context + prompt_cache_hit_tokens ───────────


def _record(monkeypatch, **kwargs):
    gw_mod = importlib.import_module("backend.app.llm.model_gateway")
    from backend.app.llm.model_profiles import ModelProfile
    from backend.app.llm.provider_registry import ProviderConfig

    captured = []
    monkeypatch.setattr(gw_mod.metrics_store, "record_task_metric", lambda rec, **kw: captured.append(rec))
    profile = ModelProfile(profile_id="p", provider="deepseek", model_name="deepseek-chat", modality="text")
    provider = ProviderConfig(name="deepseek", base_url="http://x", api_key="k")
    gw_mod.ModelGateway()._record_audit(
        task_id=None, agent_id="planner", profile=profile, provider=provider,
        duration_ms=1, input_tokens=10, output_tokens=2, ok=True, **kwargs,
    )
    return captured[0].metadata


def test_gateway_writes_manifest_and_cache_hit(monkeypatch):
    manifest = ContextManifest(caller="planner", blocks=[ContextBlock(BlockKind.SYSTEM, 100)])
    meta = _record(monkeypatch, context_manifest=manifest, usage={"prompt_cache_hit_tokens": 4672})
    assert meta["context"]["caller"] == "planner"
    assert meta["context"]["total_est"] == 100
    assert meta["context"]["cache"] == {"prompt_cache_hit_tokens": 4672}
    assert meta["profile_id"] == "p"


def test_gateway_cache_hit_null_when_missing(monkeypatch):
    meta = _record(monkeypatch, usage={"prompt_tokens": 10})
    assert meta["context"]["cache"] == {"prompt_cache_hit_tokens": None}


def test_gateway_flag_off_no_context(monkeypatch):
    monkeypatch.setenv("CONTEXT_MANIFEST_ENABLED", "false")
    meta = _record(monkeypatch, context_manifest=ContextManifest(caller="planner"))
    assert "context" not in meta


def test_cache_hit_tokens_from_usage_object():
    from types import SimpleNamespace

    from backend.app.llm.model_gateway import _cache_hit_tokens

    assert _cache_hit_tokens(SimpleNamespace(prompt_cache_hit_tokens=12)) == 12
    assert _cache_hit_tokens(SimpleNamespace(prompt_tokens=3)) is None
    assert _cache_hit_tokens(None) is None


@pytest.mark.asyncio
async def test_gateway_chat_passes_manifest_to_audit(monkeypatch):
    gw_mod = importlib.import_module("backend.app.llm.model_gateway")

    gw = gw_mod.ModelGateway()
    captured = []
    monkeypatch.setattr(gw_mod.metrics_store, "record_task_metric", lambda rec, **kw: captured.append(rec))

    async def _fake_invoke(provider, profile, messages, params):
        return "hi", {"prompt_tokens": 5, "completion_tokens": 1, "prompt_cache_hit_tokens": 3}

    monkeypatch.setattr(gw, "_invoke_chat", _fake_invoke)
    try:
        await gw.chat("KnowledgeQAAgent", [{"role": "user", "content": "x"}],
                      context_manifest=ContextManifest(caller="knowledge_qa"))
    except gw_mod.ModelInvocationError:
        pytest.skip("沙箱未配置模型供应商")
    ctx = captured[-1].metadata["context"]
    assert ctx["caller"] == "knowledge_qa"
    assert ctx["cache"]["prompt_cache_hit_tokens"] == 3


# ── 7. 工具审计 obs 字段 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_audit_obs_fields():
    reg = ToolRegistry()

    async def _big(args, ctx):
        return ToolResult(ok=True, content="x" * (OBS_MAX_CHARS + 1), data={"handle": "ds:notes:1"})

    async def _small(args, ctx):
        return ToolResult(ok=True, content="abc")

    reg.register(ToolSpec(name="big", description="", parameters={}, handler=_big))
    reg.register(ToolSpec(name="small", description="", parameters={}, handler=_small))
    await reg.execute("big", {}, ToolContext())
    await reg.execute("small", {}, ToolContext())
    big, small = reg.audit_log()
    assert (big.obs_chars, big.obs_truncated, big.obs_handle) == (OBS_MAX_CHARS + 1, True, "ds:notes:1")
    assert (small.obs_chars, small.obs_truncated, small.obs_handle) == (3, False, None)


# ── 8. _catalog.json + prompt 版本哈希 ────────────────────────────────────


def test_prompt_load_returns_version_hash():
    from backend.app.application.agents.prompts import prompt_registry

    content = prompt_registry.load("insight_summary.md")
    assert isinstance(content, str)
    assert content.version == hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    assert prompt_registry.version("insight_summary.md") == content.version


def test_catalog_registers_every_prompt_and_is_ignored_by_list_all():
    from backend.app.application.agents.prompts import prompt_registry

    catalog = prompt_registry.catalog()
    names = prompt_registry.list_all()
    assert "_catalog.json" not in names
    assert {n[:-3] for n in names} == set(catalog)
    for entry in catalog.values():
        assert entry["agent"] and entry["memory_policy"]
