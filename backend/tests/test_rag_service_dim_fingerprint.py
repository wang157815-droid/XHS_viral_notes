"""
RAGService ChromaDB collection 维度指纹机制单测(修复 2026-04-20 维度不匹配 bug)。

背景:ChromaDB 若不传 embedding_function,会用默认 all-MiniLM-L6-v2(384 维)
创建 collection 并永久锁定。切换 embedding 模型(如 text-embedding-v4 = 1024 维)后
add/query 会报 "Collection expecting embedding with dimension of 384, got 1024"。

修复方案:
- embedding_function=None 避免 ChromaDB 自动绑定默认模型
- collection metadata 写入 embedding_model 指纹
- 启动时对比当前模型,不匹配则 delete+recreate

本测试验证三条关键路径。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


class FakeEmbeddingResponse:
    """模拟 openai.embeddings.create 的返回结构。"""

    def __init__(self, dim: int, count: int):
        self.data = [type("E", (), {"embedding": [0.1] * dim})() for _ in range(count)]


class FakeEmbeddingClient:
    """假装是 openai.OpenAI 实例,支持 .embeddings.create。"""

    def __init__(self, dim: int):
        self._dim = dim
        self.calls = 0

    @property
    def embeddings(self):
        parent = self

        class _Inner:
            def create(self, *, model, input):  # noqa: A002
                parent.calls += 1
                return FakeEmbeddingResponse(parent._dim, len(input))

        return _Inner()


def _make_service(tmp_path: Path, embedding_model: str, dim: int):
    """构造 RAGService 实例,mock 掉 embedding_client / embedding_model。"""
    from viral_agent.services.knowledge.rag_service import RAGService

    svc = RAGService.__new__(RAGService)
    svc.persist_directory = str(tmp_path)
    svc.collection_name = "test_kb"
    svc.embedding_client = FakeEmbeddingClient(dim=dim)
    svc.embedding_model = embedding_model

    # 复用真实的 init 后半段(ChromaDB + 指纹检测)
    Path(svc.persist_directory).mkdir(parents=True, exist_ok=True)

    import chromadb
    from chromadb.config import Settings

    svc.client = chromadb.PersistentClient(
        path=svc.persist_directory,
        settings=Settings(anonymized_telemetry=False),
    )

    svc._ensure_collection_dimension_matches()

    collection_metadata = {
        "description": "小红书爆文知识库",
        "embedding_model": svc.embedding_model,
    }
    svc.collection = svc.client.get_or_create_collection(
        name=svc.collection_name,
        embedding_function=None,
        metadata=collection_metadata,
    )
    return svc


def test_first_init_writes_embedding_model_metadata(tmp_path):
    """首次创建 collection 时,metadata 应该记录当前 embedding_model 指纹。"""
    svc = _make_service(tmp_path, embedding_model="text-embedding-v4", dim=1024)
    assert svc.collection.metadata is not None
    assert svc.collection.metadata.get("embedding_model") == "text-embedding-v4"


def test_second_init_same_model_reuses_collection(tmp_path):
    """用相同 embedding_model 重复初始化,collection 不应被重建。"""
    svc1 = _make_service(tmp_path, embedding_model="text-embedding-v4", dim=1024)
    # 写入一条向量模拟用户数据
    svc1.collection.add(
        ids=["sentinel"],
        embeddings=[[0.0] * 1024],
        documents=["存活检查"],
        metadatas=[{"tag": "sentinel"}],
    )
    assert svc1.collection.count() == 1

    # 用同一个目录和同一个 embedding_model 再初始化一次
    svc2 = _make_service(tmp_path, embedding_model="text-embedding-v4", dim=1024)
    # sentinel 还在 → 没有重建
    assert svc2.collection.count() == 1
    assert svc2.collection.metadata.get("embedding_model") == "text-embedding-v4"


def test_different_model_triggers_rebuild(tmp_path):
    """切换 embedding_model 后,旧 collection 应被自动删除,新 collection 为空。"""
    svc1 = _make_service(tmp_path, embedding_model="text-embedding-v4", dim=1024)
    svc1.collection.add(
        ids=["old-doc"],
        embeddings=[[0.1] * 1024],
        documents=["旧数据"],
        metadatas=[{"tag": "old"}],
    )
    assert svc1.collection.count() == 1

    # 切到另一个模型(模拟 env 切换 EMBEDDING_MODEL)
    svc2 = _make_service(tmp_path, embedding_model="text-embedding-3-small", dim=1536)

    # 旧数据应该已被清空
    assert svc2.collection.count() == 0
    assert svc2.collection.metadata.get("embedding_model") == "text-embedding-3-small"

    # 用新维度写入应当成功(不会撞上 dim 锁定)
    svc2.collection.add(
        ids=["new-doc"],
        embeddings=[[0.2] * 1536],
        documents=["新数据"],
        metadatas=[{"tag": "new"}],
    )
    assert svc2.collection.count() == 1


def test_legacy_collection_without_metadata_gets_rebuilt(tmp_path):
    """模拟 4.α 旧版本遗留:collection 没有 embedding_model metadata → 必须重建。"""
    import chromadb
    from chromadb.config import Settings

    # 用老 API 形式创建 collection(不传 embedding_function,无 embedding_model metadata)
    # 直接用 default = 384 维
    client = chromadb.PersistentClient(
        path=str(tmp_path),
        settings=Settings(anonymized_telemetry=False),
    )
    legacy = client.get_or_create_collection(
        name="test_kb",
        metadata={"description": "legacy 384 维"},
    )
    # 塞一条默认 embed 数据(384 维)
    legacy.add(ids=["legacy"], documents=["some text"])
    assert legacy.count() == 1

    # 新版初始化 → 应自动识别未写指纹 → delete + recreate
    svc = _make_service(tmp_path, embedding_model="text-embedding-v4", dim=1024)
    assert svc.collection.count() == 0
    assert svc.collection.metadata.get("embedding_model") == "text-embedding-v4"
