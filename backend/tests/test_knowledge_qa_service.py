from __future__ import annotations

import pytest

from backend.app.application.conversation.knowledge_qa_service import KnowledgeQAService
from backend.app.domain.conversation import IntentClassification
from viral_agent.models.document import DocumentSearchResult


class FakeRag:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(
        self,
        query,
        domains=None,
        top_k=5,
        min_score=0.0,
        owner_user_id=None,
        scope=None,
        doc_ids=None,
    ):
        self.calls.append(
            {
                "owner_user_id": owner_user_id,
                "scope": scope,
            }
        )
        return self.results


@pytest.mark.asyncio
async def test_knowledge_qa_returns_citations(monkeypatch):
    from backend.app.application.conversation import knowledge_qa_service as mod

    async def _fake_chat(agent_id, messages, **kwargs):
        assert agent_id == "KnowledgeQAAgent"
        assert "防晒合规" in messages[-1]["content"]
        return {"content": "防晒内容需要避免绝对化功效表达。", "profile_id": "test"}

    monkeypatch.setattr(mod.model_gateway, "chat", _fake_chat)
    service = KnowledgeQAService(
        rag_factory=lambda: FakeRag(
            [
                DocumentSearchResult(
                    doc_id="doc1",
                    chunk_index=0,
                    text="防晒合规规则：避免绝对化、医疗化承诺。",
                    score=0.91,
                    metadata={"title": "护肤合规SOP"},
                )
            ]
        )
    )

    answer, citations, debug = await service.answer(
        question="知识库里防晒合规怎么说",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user={"user_id": "u1", "role": "analyst"},
    )

    assert "防晒" in answer
    assert len(citations) == 1
    assert citations[0].title == "护肤合规SOP"
    assert debug["retrieval_summary"]["vector"] == 1


@pytest.mark.asyncio
async def test_knowledge_qa_no_results_has_no_fake_citations():
    service = KnowledgeQAService(rag_factory=lambda: FakeRag([]))

    answer, citations, debug = await service.answer(
        question="知识库里不存在的问题",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user={"user_id": "u1", "role": "analyst"},
    )

    assert "没有在当前知识库中检索到" in answer
    assert citations == []
    assert debug["retrieval_summary"]["vector"] == 0


@pytest.mark.asyncio
async def test_knowledge_qa_retrieval_failure_degrades():
    def _fail_factory():
        raise RuntimeError("rag unavailable")

    service = KnowledgeQAService(rag_factory=_fail_factory)
    answer, citations, debug = await service.answer(
        question="知识库里规则怎么说",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user={"user_id": "u1", "role": "analyst"},
    )

    assert "知识库检索不可用" in answer
    assert citations == []
    assert debug["model_error_code"] == "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_knowledge_qa_passes_owner_filter_for_non_admin():
    rag = FakeRag([])
    service = KnowledgeQAService(rag_factory=lambda: rag)

    await service.answer(
        question="只检索我的知识库",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user={"user_id": "u1", "role": "analyst"},
    )

    # M1：不再传 include_all，scope 由 RAGService 取 flag 默认档位
    assert rag.calls[-1] == {"owner_user_id": "u1", "scope": None}


@pytest.mark.asyncio
async def test_knowledge_qa_admin_uses_default_scope_not_global():
    rag = FakeRag([])
    service = KnowledgeQAService(rag_factory=lambda: rag)

    await service.answer(
        question="管理员检索知识库",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user={"user_id": "admin", "role": "admin"},
    )

    # M1：admin 在对话检索中也按档位走，不再看全部
    assert rag.calls[-1] == {"owner_user_id": "admin", "scope": None}
