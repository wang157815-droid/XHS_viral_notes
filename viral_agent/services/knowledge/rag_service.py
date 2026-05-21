"""RAG service backed by PostgreSQL/pgvector."""
import os
import json
import urllib.request
from typing import List, Dict, Any, Optional
from loguru import logger
from openai import OpenAI
from dotenv import load_dotenv

from viral_agent.models.document import KnowledgeDocument, DocumentSearchResult

from backend.app.infrastructure.db.engine import get_business_db_session
from backend.app.infrastructure.db.schema import ensure_business_schema

# 加载环境变量
load_dotenv()


class RAGService:
    """RAG检索服务"""

    def __init__(
        self,
        persist_directory: str = "viral_agent/storage/chromadb",
        collection_name: str = "knowledge_base"
    ):
        """初始化 RAG 服务。

        ``persist_directory`` and ``collection_name`` are kept for backwards
        compatibility; pgvector is the only runtime backend after phase 4.6.
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._init_embedding_client()
        ensure_business_schema()
        logger.info(f"pgvector RAG 初始化成功 (embedding_model='{self.embedding_model}')")

    def _ensure_collection_dimension_matches(self) -> None:
        return None

    def _init_embedding_client(self):
        """初始化Embedding客户端"""
        # 优先使用单独配置的Embedding API，如果没有则回退到主模型配置
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.embedding_api_base = api_base.rstrip("/")
        self.ollama_embed_base = self._detect_ollama_base(self.embedding_api_base)

        if not api_key:
            logger.warning("未配置EMBEDDING_API_KEY或OPENAI_API_KEY，pgvector RAG 无法生成向量")
            self.embedding_client = None
            self.embedding_model = "default"
            return

        try:
            self.embedding_client = None if self.ollama_embed_base else OpenAI(
                api_key=api_key,
                base_url=api_base,
            )
            # 使用小模型降低成本
            self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

            # 记录使用的配置
            if os.getenv("EMBEDDING_API_KEY"):
                logger.info(f"使用单独的Embedding API配置")
            else:
                logger.info(f"使用主模型API配置")
            logger.info(f"Embedding模型: {self.embedding_model}")
            logger.info(f"Embedding API Base: {api_base}")
            if self.ollama_embed_base:
                logger.info(f"检测到 Ollama，本地向量化将使用原生 /api/embed: {self.ollama_embed_base}")
        except Exception as e:
            logger.error(f"Embedding客户端初始化失败: {e}")
            self.embedding_client = None
            self.embedding_model = "default"

    def add_document(
        self,
        doc: KnowledgeDocument,
        auto_chunk: bool = True
    ) -> bool:
        """
        添加文档到向量库

        Args:
            doc: 知识文档对象
            auto_chunk: 是否自动分块

        Returns:
            是否成功
        """
        try:
            # 文本分块
            if auto_chunk and not doc.chunks:
                from viral_agent.services.knowledge.document_parser import DocumentParser
                parser = DocumentParser()
                doc.chunks = parser.split_text(doc.content, chunk_size=500, overlap=50)

            if not doc.chunks:
                doc.chunks = [doc.content]

            logger.info(f"添加文档: {doc.title}, 分块数: {len(doc.chunks)}")

            embeddings = self._get_embeddings(doc.chunks)
            self.upsert_chunks(
                doc_id=doc.doc_id,
                title=doc.title,
                chunks=doc.chunks,
                domains=doc.domains,
                embeddings=embeddings,
                base_metadata={
                    'doc_id': doc.doc_id,
                    'title': doc.title,
                    'description': doc.description,
                    'filename': doc.metadata.filename,
                    'format': doc.metadata.format,
                    'upload_time': doc.metadata.upload_time,
                },
            )

            logger.success(f"文档添加成功: {doc.title}")
            return True

        except Exception as e:
            logger.error(f"添加文档失败 {doc.title}: {e}")
            return False

    def search(
        self,
        query: str,
        domains: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 0.0,
        owner_user_id: Optional[str] = None,
        include_all: bool = True,
        doc_ids: Optional[List[str]] = None,
    ) -> List[DocumentSearchResult]:
        """
        语义搜索知识库

        Args:
            query: 查询问题
            domains: 限定领域（可选）
            top_k: 返回结果数
            min_score: 最小相似度分数

        Returns:
            搜索结果列表
        """
        try:
            query_embedding = self._get_embeddings([query])[0]
            params: Dict[str, Any] = {
                "embedding": _format_vector(query_embedding),
                "top_k": top_k,
                "embedding_model": self.embedding_model,
                "owner_user_id": owner_user_id or "",
            }
            domain_clause = ""
            if domains:
                domain_clause = "AND c.domains ?| :domains"
                params["domains"] = domains
            owner_clause = ""
            join_clause = ""
            if not include_all:
                join_clause = "JOIN knowledge_documents d ON d.doc_id = c.doc_id"
                owner_clause = "AND d.owner_user_id = :owner_user_id"
            doc_clause = ""
            if doc_ids:
                cleaned = [str(d).strip() for d in doc_ids if str(d).strip()]
                if cleaned:
                    ph = ",".join([f":doc_{i}" for i in range(len(cleaned))])
                    for i, d in enumerate(cleaned):
                        params[f"doc_{i}"] = d
                    doc_clause = f"AND c.doc_id IN ({ph})"

            with get_business_db_session() as session:
                rows = session.execute(
                    _sql_text(
                        f"""
                        SELECT c.doc_id, c.chunk_index, c.text, c.metadata,
                               1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
                        FROM knowledge_chunks c
                        {join_clause}
                        WHERE c.embedding IS NOT NULL
                          AND c.embedding_model = :embedding_model
                          {domain_clause}
                          {owner_clause}
                          {doc_clause}
                        ORDER BY c.embedding <=> CAST(:embedding AS vector)
                        LIMIT :top_k
                        """
                    ),
                    params,
                ).mappings().all()
            results = [
                DocumentSearchResult(
                    doc_id=str(row["doc_id"]),
                    chunk_index=int(row["chunk_index"]),
                    text=str(row["text"] or ""),
                    score=round(float(row["score"] or 0), 4),
                    metadata=dict(row["metadata"] or {}),
                )
                for row in rows
                if float(row["score"] or 0) >= min_score
            ]
            return results[:top_k]

        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    def delete_document(self, doc_id: str) -> bool:
        """
        删除文档

        Args:
            doc_id: 文档ID

        Returns:
            是否成功
        """
        try:
            with get_business_db_session() as session:
                result = session.execute(
                    _sql_text("DELETE FROM knowledge_chunks WHERE doc_id = :doc_id"),
                    {"doc_id": doc_id},
                )
            deleted = int(result.rowcount or 0)
            logger.info(f"删除文档向量: {doc_id}, 删除{deleted}个块")
            return deleted > 0

        except Exception as e:
            logger.error(f"删除文档失败 {doc_id}: {e}")
            return False

    def list_all_documents(
        self,
        domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        列出所有文档

        Args:
            domain_filter: 领域过滤（可选）

        Returns:
            文档列表
        """
        try:
            params: Dict[str, Any] = {}
            where = ""
            if domain_filter:
                where = "WHERE domains ? :domain_filter"
                params["domain_filter"] = domain_filter
            with get_business_db_session() as session:
                rows = session.execute(
                    _sql_text(
                        f"""
                        SELECT doc_id,
                               MIN(metadata ->> 'title') AS title,
                               MIN(metadata ->> 'description') AS description,
                               MIN(metadata ->> 'filename') AS filename,
                               MIN(metadata ->> 'format') AS format,
                               MIN(metadata ->> 'upload_time') AS upload_time,
                               MIN(domains::text) AS domains_text
                        FROM knowledge_chunks
                        {where}
                        GROUP BY doc_id
                        ORDER BY doc_id
                        LIMIT 1000
                        """
                    ),
                    params,
                ).mappings().all()
            docs = []
            for row in rows:
                try:
                    domains = json.loads(row.get("domains_text") or "[]")
                except json.JSONDecodeError:
                    domains = []
                docs.append(
                    {
                        "doc_id": row.get("doc_id"),
                        "title": row.get("title") or "",
                        "description": row.get("description") or "",
                        "domains": domains,
                        "filename": row.get("filename") or "",
                        "format": row.get("format") or "",
                        "upload_time": row.get("upload_time") or "",
                    }
                )
            return docs

        except Exception as e:
            logger.error(f"列出文档失败: {e}")
            return []

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """生成文本向量"""
        if self.ollama_embed_base:
            return self._get_ollama_embeddings(texts)

        if not self.embedding_client:
            raise ValueError("Embedding客户端未初始化")

        try:
            response = self.embedding_client.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"生成向量失败: {e}")
            raise

    def _get_ollama_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Use Ollama native /api/embed because /v1/embeddings can return 502 locally."""
        url = f"{self.ollama_embed_base}/api/embed"
        payload = json.dumps(
            {"model": self.embedding_model, "input": texts},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.error(f"Ollama 原生向量化失败: {exc}")
            raise
        embeddings = raw.get("embeddings") or []
        if not embeddings:
            raise ValueError("Ollama /api/embed 未返回 embeddings")
        if len(embeddings) != len(texts):
            raise ValueError(f"Ollama embeddings 数量不匹配: {len(embeddings)} != {len(texts)}")
        return embeddings

    @staticmethod
    def _detect_ollama_base(api_base: str) -> Optional[str]:
        base = (api_base or "").rstrip("/")
        lowered = base.lower()
        if "localhost:11434" not in lowered and "127.0.0.1:11434" not in lowered:
            return None
        if lowered.endswith("/v1"):
            return base[:-3].rstrip("/")
        return base

    def _format_results(
        self,
        results: Dict[str, Any],
        min_score: float = 0.0
    ) -> List[DocumentSearchResult]:
        """格式化搜索结果"""
        formatted = []

        if not results['ids'] or not results['ids'][0]:
            return formatted

        for i in range(len(results['ids'][0])):
            # ChromaDB返回的是距离，需要转换为相似度分数
            distance = results['distances'][0][i] if 'distances' in results else 0
            score = 1 - distance  # 距离越小，相似度越高

            if score < min_score:
                continue

            metadata = results['metadatas'][0][i]

            result = DocumentSearchResult(
                doc_id=metadata.get('doc_id', ''),
                chunk_index=metadata.get('chunk_index', 0),
                text=results['documents'][0][i],
                score=round(score, 4),
                metadata=metadata
            )

            formatted.append(result)

        return formatted

    def upsert_chunks(
        self,
        *,
        doc_id: str,
        title: str,
        chunks: List[str],
        domains: List[str],
        embeddings: List[List[float]],
        base_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        if not chunks:
            return 0
        if len(embeddings) != len(chunks):
            raise ValueError("embeddings 数量与 chunks 不一致")
        base_metadata = base_metadata or {}
        rows = []
        for idx, chunk in enumerate(chunks):
            metadata = {
                **base_metadata,
                "doc_id": doc_id,
                "title": title,
                "chunk_index": idx,
                "domains": json.dumps(domains, ensure_ascii=False),
            }
            rows.append(
                {
                    "chunk_id": f"{doc_id}:chunk:{idx}",
                    "doc_id": doc_id,
                    "chunk_index": idx,
                    "text": chunk,
                    "domains": json.dumps(domains, ensure_ascii=False),
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "embedding_model": self.embedding_model,
                    "embedding": _format_vector(embeddings[idx]),
                }
            )
        with get_business_db_session() as session:
            session.execute(
                _sql_text("DELETE FROM knowledge_chunks WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            session.execute(
                _sql_text(
                    """
                    INSERT INTO knowledge_chunks(
                        chunk_id, doc_id, chunk_index, text, domains, metadata,
                        embedding_model, embedding, updated_at
                    ) VALUES (
                        :chunk_id, :doc_id, :chunk_index, :text,
                        CAST(:domains AS jsonb), CAST(:metadata AS jsonb),
                        :embedding_model, CAST(:embedding AS vector), NOW()
                    )
                    ON CONFLICT (doc_id, chunk_index) DO UPDATE SET
                        text = EXCLUDED.text,
                        domains = EXCLUDED.domains,
                        metadata = EXCLUDED.metadata,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """
                ),
                rows,
            )
        return len(rows)


def _format_vector(vector: List[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def _sql_text(sql: str):
    from sqlalchemy import text

    return text(sql)
