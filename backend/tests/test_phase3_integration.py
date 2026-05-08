"""阶段3 集成测试：知识库 CRUD + 文档上传/删除 + 任务重试。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_tmp(tmp_path: Path, monkeypatch):
    """用临时目录隔离 KnowledgeRegistry 和 TaskRepository。"""
    # 隔离知识库存储
    from backend.app.services import knowledge_registry as kr_mod
    from backend.app.services.knowledge_registry import KnowledgeRegistry

    fresh_registry = KnowledgeRegistry(root=tmp_path / "kb")
    monkeypatch.setattr(kr_mod, "knowledge_registry", fresh_registry)

    from backend.app.api.routes import knowledge as knowledge_route
    monkeypatch.setattr(knowledge_route, "knowledge_registry", fresh_registry)

    # 禁用向量化（测试环境无 embedding key）
    async def _skip_vectorize(**kwargs):
        return "skipped", "test_env_skip"

    monkeypatch.setattr(knowledge_route, "_maybe_vectorize", _skip_vectorize)

    # 伪造登录 current_user（通过 FastAPI 依赖覆盖，不改模块属性）
    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_user():
        return {"user_id": "user_test", "nickname": "测试", "role": "admin", "username": "test"}

    app.dependency_overrides[get_current_user] = _fake_user

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_domain_crud_persists(client_with_tmp: TestClient):
    # 列表初始为空
    r = client_with_tmp.get("/api/v1/knowledge/domains")
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []

    # 创建
    r = client_with_tmp.post(
        "/api/v1/knowledge/domains",
        json={"name": "测试领域", "keywords": ["a", "b"], "priority": "high", "enabled": True},
    )
    assert r.status_code == 200
    record = r.json()["data"]
    assert record["name"] == "测试领域"
    assert record["rule_count"] == 2
    domain_id = record["domain_id"]

    # 列表含 1 项
    r = client_with_tmp.get("/api/v1/knowledge/domains")
    assert len(r.json()["data"]["items"]) == 1

    # 更新
    r = client_with_tmp.put(
        f"/api/v1/knowledge/domains/{domain_id}",
        json={"name": "更新名", "keywords": ["c"], "priority": "low", "enabled": False},
    )
    assert r.status_code == 200
    updated = r.json()["data"]
    assert updated["name"] == "更新名"
    assert updated["enabled"] is False

    # 删除
    r = client_with_tmp.delete(f"/api/v1/knowledge/domains/{domain_id}")
    assert r.status_code == 200
    r = client_with_tmp.get("/api/v1/knowledge/domains")
    assert r.json()["data"]["items"] == []


def test_document_upload_and_delete(client_with_tmp: TestClient):
    # 准备一个 txt 文件
    content = "这是一段测试文本。\n这是第二段。" * 20
    file = io.BytesIO(content.encode("utf-8"))

    r = client_with_tmp.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("demo.txt", file, "text/plain")},
        data={"domain_ids": ""},
    )
    assert r.status_code == 200, r.text
    doc = r.json()["data"]
    assert doc["name"] == "demo.txt"
    assert doc["format"] == "txt"
    assert doc["size_bytes"] > 0
    assert doc["vector_status"] == "skipped"  # 测试环境强制跳过向量化
    assert doc["chunks"] >= 1
    doc_id = doc["doc_id"]

    # 列表可见
    r = client_with_tmp.get("/api/v1/knowledge/documents")
    items = r.json()["data"]["items"]
    assert any(d["doc_id"] == doc_id for d in items)

    # 删除
    r = client_with_tmp.delete(f"/api/v1/knowledge/documents/{doc_id}")
    assert r.status_code == 200

    r = client_with_tmp.get("/api/v1/knowledge/documents")
    assert not any(d["doc_id"] == doc_id for d in r.json()["data"]["items"])


def test_document_upload_rejects_oversize(client_with_tmp: TestClient):
    big = io.BytesIO(b"0" * (11 * 1024 * 1024))
    r = client_with_tmp.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("big.txt", big, "text/plain")},
        data={"domain_ids": ""},
    )
    assert r.status_code == 413


def test_document_upload_rejects_unsupported_ext(client_with_tmp: TestClient):
    f = io.BytesIO(b"data")
    r = client_with_tmp.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("x.exe", f, "application/octet-stream")},
        data={"domain_ids": ""},
    )
    assert r.status_code == 400


@pytest.fixture
def non_admin_client(tmp_path: Path, monkeypatch):
    """同 client_with_tmp，但身份是普通用户。"""
    from backend.app.services import knowledge_registry as kr_mod
    from backend.app.services.knowledge_registry import KnowledgeRegistry

    fresh_registry = KnowledgeRegistry(root=tmp_path / "kb_nonadmin")
    monkeypatch.setattr(kr_mod, "knowledge_registry", fresh_registry)

    from backend.app.api.routes import knowledge as knowledge_route
    monkeypatch.setattr(knowledge_route, "knowledge_registry", fresh_registry)

    async def _skip_vectorize(**kwargs):
        return "skipped", "test_env_skip"

    monkeypatch.setattr(knowledge_route, "_maybe_vectorize", _skip_vectorize)

    from backend.app.core.security import get_current_user
    from backend.app.main import app

    def _fake_user_plain():
        return {"user_id": "user_plain", "nickname": "普通", "role": "user", "username": "plain"}

    app.dependency_overrides[get_current_user] = _fake_user_plain

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_chunks_endpoint_requires_admin(non_admin_client: TestClient):
    """普通用户访问 /chunks 应 403，且上传/删除等基础操作仍可用。"""
    # 先用普通用户身份上传（基础操作不要求 admin）
    content = "hello world"
    file = io.BytesIO(content.encode("utf-8"))
    r = non_admin_client.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("demo.txt", file, "text/plain")},
        data={"domain_ids": ""},
    )
    assert r.status_code == 200
    doc_id = r.json()["data"]["doc_id"]

    # 普通用户访问 /chunks → 403
    r = non_admin_client.get(f"/api/v1/knowledge/documents/{doc_id}/chunks")
    assert r.status_code == 403

    # 普通用户访问 /search → 403
    r = non_admin_client.post(
        "/api/v1/knowledge/search",
        json={"query": "hello", "top_k": 3},
    )
    assert r.status_code == 403


def test_fetch_chunks_fallback_from_disk(client_with_tmp: TestClient):
    """未向量化的文档 → 应能从磁盘重解析出分块。"""
    content = "第一段。" * 80 + "\n\n" + "第二段。" * 80
    file = io.BytesIO(content.encode("utf-8"))

    r = client_with_tmp.post(
        "/api/v1/knowledge/documents/upload",
        files={"file": ("fallback.txt", file, "text/plain")},
        data={"domain_ids": ""},
    )
    assert r.status_code == 200
    doc = r.json()["data"]
    assert doc["vector_status"] == "skipped"  # 测试 fixture 跳过向量化
    doc_id = doc["doc_id"]

    r = client_with_tmp.get(f"/api/v1/knowledge/documents/{doc_id}/chunks")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["source"] == "reparsed"
    assert body["total"] >= 1
    assert body["chunks"][0]["text"]
    assert body["chunks"][0]["chunk_index"] == 0
