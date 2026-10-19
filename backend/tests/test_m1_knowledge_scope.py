"""M1：知识库检索范围 + visibility（设计 v1.1 §G.M1）。

- 单元测试：档位 SQL、失败关闭、QA 空用户、visibility 接口权限、schema 静态检查。
- 集成测试（需要可连的 PostgreSQL+pgvector，否则 skip）：在**临时 schema** 里执行
  business_schema_sql，跑真实的 RAGService.search SQL（embedding 用固定向量替身），
  结束后 DROP SCHEMA，不触碰业务表。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List

import pytest

from backend.app.services import knowledge_scope as ks


VEC_DIM = 3


@dataclass
class _User:
    user_id: str
    role: str


class _FakeUserStore:
    def __init__(self, users: List[_User], fail: bool = False) -> None:
        self.users = users
        self.fail = fail

    def list_users(self):
        if self.fail:
            raise RuntimeError("user store unavailable")
        return list(self.users)


@pytest.fixture
def user_store(monkeypatch):
    """替换用户存储（admin 的 user_id 形如 u_xxx），并清空 admin 名单缓存。"""
    from backend.app.services.redmuse_auth import user_store as us_mod

    store = _FakeUserStore([_User("u_admin01", "admin"), _User("u_a", "analyst"), _User("u_b", "analyst")])
    monkeypatch.setattr(us_mod, "_default_store", store)
    ks.clear_admin_ids_cache()
    yield store
    ks.clear_admin_ids_cache()


@pytest.fixture
def warnings_log():
    from loguru import logger

    messages: List[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    yield messages
    logger.remove(sink_id)


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_default_scope_is_owner_or_admin(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_SEARCH_SCOPE", raising=False)
    assert ks.default_scope() == "owner_or_admin"
    monkeypatch.setenv("KNOWLEDGE_SEARCH_SCOPE", "strict")
    assert ks.default_scope() == "strict"
    with pytest.raises(ValueError):
        ks.resolve_scope("everything")


def test_t1_5_strict_sql_has_owner_or_shared_and_no_unowned_branch(user_store):
    clause, params = ks.build_scope_filter("strict", "u_a")
    assert "owner_user_id = :owner_user_id OR d.visibility = 'shared'" in clause
    assert "admin" not in clause  # strict 不使用 admin 概念
    assert params == {"owner_user_id": "u_a"}
    # 除 legacy 外，所有档位（含失败关闭分支）都必须带 owner 条件
    for scope in ("strict", "owner_or_admin"):
        clause, _ = ks.build_scope_filter(scope, "u_a")
        assert ":owner_user_id" in clause
    user_store.fail = True
    ks.clear_admin_ids_cache()
    clause, _ = ks.build_scope_filter("owner_or_admin", "u_a")
    assert clause == "AND d.owner_user_id = :owner_user_id"


def test_t1_5_rag_service_search_sql_uses_scope_clause(monkeypatch):
    """RAGService.search 实际执行的 SQL 带 strict 子句，且总是 JOIN knowledge_documents。"""
    from viral_agent.services.knowledge import rag_service as rs

    captured: Dict[str, Any] = {}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class _Session:
        def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    @contextmanager
    def _fake_session():
        yield _Session()

    monkeypatch.setattr(rs, "get_business_db_session", _fake_session)
    rag = rs.RAGService.__new__(rs.RAGService)
    rag.embedding_model = "fake"
    rag._get_embeddings = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]

    rag.search("q", owner_user_id="u_a", scope="strict")
    sql = " ".join(captured["sql"].split())
    assert "JOIN knowledge_documents d ON d.doc_id = c.doc_id" in sql
    assert "AND (d.owner_user_id = :owner_user_id OR d.visibility = 'shared')" in sql
    assert captured["params"]["owner_user_id"] == "u_a"


def test_owner_or_admin_params_bound_not_interpolated(user_store):
    clause, params = ks.build_scope_filter("owner_or_admin", "u_a")
    assert "u_admin01" not in clause and "'admin'" not in clause
    assert params["admin_ids"] == ["u_admin01"]
    assert params["legacy_admin_owner"] == "admin"


def test_admin_ids_cached_and_failure_not_cached(user_store, monkeypatch):
    assert ks.admin_user_ids() == ["u_admin01"]
    user_store.users = []  # 缓存期内名单变化不立即生效
    assert ks.admin_user_ids() == ["u_admin01"]
    ks.clear_admin_ids_cache()
    user_store.fail = True
    with pytest.raises(RuntimeError):
        ks.admin_user_ids()
    user_store.fail = False
    assert ks.admin_user_ids() == []


def test_t1_7_user_store_failure_fails_closed_with_warning(user_store, warnings_log):
    user_store.fail = True
    clause, params = ks.build_scope_filter("owner_or_admin", "u_a")
    assert clause == "AND d.owner_user_id = :owner_user_id"
    assert params == {"owner_user_id": "u_a"}
    assert any("失败关闭" in m for m in warnings_log)


@pytest.mark.asyncio
async def test_t1_3_qa_without_current_user_returns_empty_and_warns(warnings_log):
    from backend.app.application.conversation.knowledge_qa_service import KnowledgeQAService
    from backend.app.domain.conversation import IntentClassification

    def _factory():
        raise AssertionError("current_user 为空时不应创建 RAG")

    service = KnowledgeQAService(rag_factory=_factory)
    answer, citations, debug = await service.answer(
        question="知识库里规则怎么说",
        intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
        current_user=None,
    )
    assert citations == []
    assert debug["retrieval_summary"]["vector"] == 0
    assert any("current_user 为空" in m for m in warnings_log)


@pytest.fixture
def visibility_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.api.routes import knowledge as knowledge_route
    from backend.app.core.security import get_current_user
    from backend.app.main import app
    from backend.app.services.knowledge_registry import KnowledgeRegistry

    registry = KnowledgeRegistry(root=tmp_path / "kb")
    monkeypatch.setattr(knowledge_route, "knowledge_registry", registry)
    audits: List[Any] = []
    monkeypatch.setattr(knowledge_route.metrics_store, "record_task_metric", lambda rec, **kw: audits.append(rec))
    state = {"user": {"user_id": "u_a", "role": "analyst", "username": "a", "nickname": "a"}}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    try:
        yield TestClient(app), registry, state, audits
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _register(registry, owner: str) -> str:
    return registry.register_document(
        filename=f"{owner}.txt", raw_bytes=b"x", size_bytes=1, chunks=1, keywords=[], domains=[],
        vector_status="skipped", uploaded_by=owner, owner_user_id=owner,
    )["doc_id"]


def test_t1_4_non_admin_visibility_api_forbidden(visibility_client):
    client, registry, state, audits = visibility_client
    doc_id = _register(registry, "u_admin01")
    r = client.patch(f"/api/v1/knowledge/documents/{doc_id}/visibility", json={"visibility": "shared"})
    assert r.status_code == 403
    r = client.post("/api/v1/knowledge/documents/visibility/batch", json={"doc_ids": [doc_id], "visibility": "shared"})
    assert r.status_code == 403
    assert registry.get_document(doc_id)["visibility"] == "private"
    assert audits == []


def test_admin_sets_visibility_with_audit_and_list_shows_shared_read_only(visibility_client):
    client, registry, state, audits = visibility_client
    admin_doc = _register(registry, "u_admin01")
    other_doc = _register(registry, "u_admin01")
    own_doc = _register(registry, "u_a")
    b_doc = _register(registry, "u_b")

    state["user"] = {"user_id": "u_admin01", "role": "admin", "username": "admin", "nickname": "admin"}
    r = client.patch(f"/api/v1/knowledge/documents/{admin_doc}/visibility", json={"visibility": "shared"})
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/v1/knowledge/documents/visibility/batch",
        json={"doc_ids": [other_doc, "doc_missing"], "visibility": "private"},
    )
    assert r.json()["data"]["not_found"] == ["doc_missing"]
    assert [a.metric_type for a in audits] == ["knowledge_visibility_change"] * 2
    assert audits[0].metadata == {"doc_id": admin_doc, "from": "private", "to": "shared", "actor_user_id": "u_admin01"}
    assert client.patch(f"/api/v1/knowledge/documents/{admin_doc}/visibility", json={"visibility": "public"}).status_code == 422

    state["user"] = {"user_id": "u_a", "role": "analyst", "username": "a", "nickname": "a"}
    items = {i["doc_id"]: i for i in client.get("/api/v1/knowledge/documents").json()["data"]["items"]}
    assert set(items) == {admin_doc, own_doc}
    assert items[admin_doc]["read_only"] is True and items[admin_doc]["visibility"] == "shared"
    assert items[own_doc]["read_only"] is False
    assert b_doc not in items
    # shared 文档分块只读可看；他人 private 仍 403
    assert client.get(f"/api/v1/knowledge/documents/{b_doc}/chunks").status_code == 403


def test_t1_8_static_schema_never_overwrites_visibility(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VECTOR_DIM", str(VEC_DIM))
    from backend.app.infrastructure.db.schema import _split_sql, business_schema_sql

    stmts = [s for s in _split_sql(business_schema_sql()) if "visibility" in s.lower()]
    assert len(stmts) == 1
    assert stmts[0].startswith("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS visibility")
    assert "DEFAULT 'private'" in stmts[0]


# ---------------------------------------------------------------------------
# 集成测试：临时 schema 中的真实 SQL
# ---------------------------------------------------------------------------
@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_VECTOR_DIM", str(VEC_DIM))
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        from backend.app.infrastructure.db.engine import BusinessPostgresSettings
    except ImportError:
        pytest.skip("sqlalchemy 不可用")
    schema = f"m1test_{uuid.uuid4().hex[:10]}"
    dsn = BusinessPostgresSettings.from_env().dsn
    admin_engine = create_engine(dsn, connect_args={"connect_timeout": 3}, future=True)
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    except Exception as exc:  # noqa: BLE001
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL 不可用: {exc}")
    engine = create_engine(
        dsn, connect_args={"connect_timeout": 3, "options": f"-csearch_path={schema},public"}, future=True
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    from backend.app.infrastructure.db.schema import _split_sql, business_schema_sql

    def run_schema():
        with engine.begin() as conn:
            for stmt in _split_sql(business_schema_sql()):
                conn.execute(text(stmt))

    from backend.app.services import knowledge_registry as kr_mod
    from viral_agent.services.knowledge import rag_service as rs

    monkeypatch.setattr(rs, "get_business_db_session", session_ctx)
    monkeypatch.setattr(kr_mod, "get_business_db_session", session_ctx)
    run_schema()

    rag = rs.RAGService.__new__(rs.RAGService)
    rag.embedding_model = "fake"
    rag._get_embeddings = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]

    def add_doc(doc_id: str, owner: str, with_chunk: bool = True):
        with session_ctx() as s:
            s.execute(
                text("INSERT INTO knowledge_documents(doc_id, name, owner_user_id, uploaded_by) VALUES (:d, :d, :o, :o)"),
                {"d": doc_id, "o": owner},
            )
        if with_chunk:
            rag.upsert_chunks(doc_id=doc_id, title=doc_id, chunks=[f"text of {doc_id}"], domains=[], embeddings=[[1.0, 0.0, 0.0]])

    def visibility(doc_id: str) -> str:
        with session_ctx() as s:
            return s.execute(text("SELECT visibility FROM knowledge_documents WHERE doc_id = :d"), {"d": doc_id}).scalar_one()

    env = {"rag": rag, "add_doc": add_doc, "run_schema": run_schema, "visibility": visibility,
           "session": session_ctx, "text": text, "registry": kr_mod.SqlAlchemyKnowledgeRegistry.__new__(kr_mod.SqlAlchemyKnowledgeRegistry)}
    try:
        yield env
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin_engine.dispose()


def _ids(results) -> set:
    return {r.doc_id for r in results}


def test_t1_1_users_cannot_see_each_other_via_tool_and_qa(pg, user_store, monkeypatch):
    pg["add_doc"]("doc_a", "u_a")
    pg["add_doc"]("doc_b", "u_b")
    rag = pg["rag"]
    for scope in ("owner_or_admin", "strict"):
        assert _ids(rag.search("q", owner_user_id="u_a", scope=scope)) == {"doc_a"}
    assert _ids(rag.search("q", owner_user_id="u_a", scope="legacy")) == {"doc_a", "doc_b"}

    import asyncio

    from backend.app.application.agent_runtime.tool_registry import ToolContext
    from backend.app.application.agent_runtime.tools import knowledge as tool_mod
    from backend.app.application.conversation.knowledge_qa_service import KnowledgeQAService
    from backend.app.domain.conversation import IntentClassification

    monkeypatch.setattr(tool_mod, "_get_rag", lambda: rag)
    for scope in ("owner_or_admin", "strict"):
        monkeypatch.setenv("KNOWLEDGE_SEARCH_SCOPE", scope)
        ctx = ToolContext(owner_user_id="u_a")
        res = asyncio.run(tool_mod._search_knowledge({"query": "q"}, ctx))
        assert {i["doc_id"] for i in res.data["results"]} == {"doc_a"}

        captured: Dict[str, Any] = {}

        async def _prompt():
            return await KnowledgeQAService(rag_factory=lambda: rag).prepare_stream_prompt(
                question="q",
                intent=IntentClassification(intent="knowledge_qa", should_retrieve_knowledge=True),
                current_user={"user_id": "u_a", "role": "analyst"},
            )

        _, citations, _ = asyncio.run(_prompt())
        captured["docs"] = {c.doc_id for c in citations}
        assert captured["docs"] == {"doc_a"}


def test_t1_2_admin_private_and_legacy_admin_docs(pg, user_store):
    rag = pg["rag"]
    pg["add_doc"]("doc_a", "u_a")
    pg["add_doc"]("doc_admin", "u_admin01")
    pg["add_doc"]("doc_legacy", "admin")  # 历史默认 owner 字面量
    assert _ids(rag.search("q", owner_user_id="u_a", scope="owner_or_admin")) == {"doc_a", "doc_admin", "doc_legacy"}
    assert _ids(rag.search("q", owner_user_id="u_a", scope="strict")) == {"doc_a"}

    pg["registry"].set_visibility(["doc_admin"], "shared")
    results = {r.doc_id: r for r in rag.search("q", owner_user_id="u_a", scope="strict")}
    assert set(results) == {"doc_a", "doc_admin"}  # 字面量 'admin' 老文档在 strict 下仍是 private
    assert results["doc_admin"].trust == "shared_doc"
    assert results["doc_a"].trust == "owner_doc"


def test_owner_or_admin_with_empty_admin_ids(pg, user_store):
    user_store.users = [_User("u_a", "analyst")]
    pg["add_doc"]("doc_a", "u_a")
    pg["add_doc"]("doc_admin", "u_admin01")
    pg["add_doc"]("doc_legacy", "admin")
    assert _ids(pg["rag"].search("q", owner_user_id="u_a", scope="owner_or_admin")) == {"doc_a", "doc_legacy"}


def test_t1_7_user_store_failure_only_own_docs(pg, user_store, warnings_log):
    user_store.fail = True
    pg["add_doc"]("doc_a", "u_a")
    pg["add_doc"]("doc_admin", "u_admin01")
    pg["add_doc"]("doc_legacy", "admin")
    assert _ids(pg["rag"].search("q", owner_user_id="u_a", scope="owner_or_admin")) == {"doc_a"}
    assert any("失败关闭" in m for m in warnings_log)


def test_rag_agent_strict_excludes_admin_private_docs(pg, user_store, monkeypatch):
    """修订 1：流水线 RAGAgent 一开始就是 strict，admin 私有文档不进普通用户命中。"""
    import asyncio

    from backend.app.application.agents import rag_agent as rag_mod
    from backend.app.application.agents.base import AgentContext
    from backend.app.domain.task_context import TaskContext, TaskContextWriter
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository.task_repository import TaskRecord

    pg["add_doc"]("doc_a", "u_a")
    pg["add_doc"]("doc_admin", "u_admin01")
    pg["add_doc"]("doc_legacy", "admin")
    monkeypatch.setenv("KNOWLEDGE_SEARCH_SCOPE", "owner_or_admin")  # 过渡档不影响 RAGAgent
    monkeypatch.setattr(rag_mod, "_get_rag_service", lambda: pg["rag"])
    monkeypatch.setattr(
        rag_mod.task_repository, "get",
        lambda task_id: TaskRecord(task_id=task_id, owner_user_id="u_a", status=TaskStatus.RUNNING),
    )

    class _Bus:
        async def publish_event(self, **kwargs):
            return None

    class _Gateway:
        async def chat(self, **kwargs):
            return {"content": '{"rewritten_queries":["q"]}'}

    ctx = TaskContext(task_id="task-m1-rag")
    TaskContextWriter(ctx).write("input_spec", {"raw_input": "q", "parsed": {"keywords": ["q"]}}, agent_id="test")
    agent = rag_mod.RAGAgent(model_gateway_instance=_Gateway(), event_bus=_Bus())
    result = asyncio.run(agent.run(AgentContext(task_id=ctx.task_id, task_context=ctx)))
    assert {h["doc_id"] for h in result.output["hits"]} == {"doc_a"}


def test_t1_6_migration_backfills_existing_rows_private(pg):
    from backend.app.infrastructure.db.schema import _split_sql, knowledge_visibility_sql

    text = pg["text"]
    with pg["session"]() as s:
        s.execute(text("ALTER TABLE knowledge_documents DROP COLUMN visibility"))
    pg["add_doc"]("doc_old1", "admin", with_chunk=False)
    pg["add_doc"]("doc_old2", "u_a", with_chunk=False)
    with pg["session"]() as s:  # 与 Alembic 20261019_0003.upgrade 执行的语句相同
        for stmt in _split_sql(knowledge_visibility_sql()):
            s.execute(text(stmt))
        rows = s.execute(text("SELECT visibility, count(*) FROM knowledge_documents GROUP BY 1")).all()
    assert [tuple(r) for r in rows] == [("private", 2)]
    with pytest.raises(Exception):
        with pg["session"]() as s:
            s.execute(text("UPDATE knowledge_documents SET visibility = 'public'"))


def test_t1_8_rerunning_ensure_schema_keeps_visibility(pg):
    pg["add_doc"]("doc_s", "u_admin01", with_chunk=False)
    pg["add_doc"]("doc_p", "u_a", with_chunk=False)
    pg["registry"].set_visibility(["doc_s"], "shared")
    pg["run_schema"]()
    pg["run_schema"]()
    assert pg["visibility"]("doc_s") == "shared"
    assert pg["visibility"]("doc_p") == "private"
