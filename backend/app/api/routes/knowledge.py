"""
知识库 API：
- /domains：领域规则 CRUD（PostgreSQL 持久化）
- /documents：文档列表 / 上传（multipart） / 删除
  · 上传时调用 viral_agent.services.knowledge.document_parser 解析 + 分块
  · 可选向量化：如果 Embedding + pgvector 可用则自动入库，否则跳过不阻塞
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from ...core.responses import ok
from ...core.security import get_current_user, require_admin_user
from ...infrastructure.db.engine import get_business_db_session
from ...services.knowledge_registry import knowledge_registry


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "md", "markdown", "txt"}


# ---------------------------------------------------------------------------
# 领域规则 CRUD
# ---------------------------------------------------------------------------
class DomainPayload(BaseModel):
    name: str = Field(..., min_length=1)
    keywords: List[str] = Field(default_factory=list)
    priority: str = "medium"
    enabled: bool = True


@router.get("/domains")
async def list_domains():
    return ok({"items": knowledge_registry.list_domains()})


@router.post("/domains")
async def create_domain(
    payload: DomainPayload,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    record = knowledge_registry.create_domain(
        name=payload.name.strip(),
        keywords=[k.strip() for k in payload.keywords if k and k.strip()],
        priority=payload.priority,
        enabled=payload.enabled,
    )
    return ok(record)


@router.put("/domains/{domain_id}")
async def update_domain(
    domain_id: str,
    payload: DomainPayload,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    record = knowledge_registry.update_domain(
        domain_id,
        name=payload.name.strip(),
        keywords=[k.strip() for k in payload.keywords if k and k.strip()],
        priority=payload.priority,
        enabled=payload.enabled,
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"领域不存在: {domain_id}")
    return ok(record)


@router.delete("/domains/{domain_id}")
async def delete_domain(
    domain_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    if not knowledge_registry.delete_domain(domain_id):
        raise HTTPException(status_code=404, detail=f"领域不存在: {domain_id}")
    return ok({"domain_id": domain_id, "deleted": True})


# ---------------------------------------------------------------------------
# 文档列表 / 删除
# ---------------------------------------------------------------------------
@router.get("/documents")
async def list_documents(domain_id: Optional[str] = None):
    return ok({"items": knowledge_registry.list_documents(domain_id=domain_id)})


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    record = knowledge_registry.delete_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return ok({"doc_id": doc_id, "deleted": True})


# ---------------------------------------------------------------------------
# 文档上传（multipart）
# ---------------------------------------------------------------------------
@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    domain_ids: str = Form(default=""),  # 逗号分隔的 domain_id 列表
    current_user: dict = Depends(get_current_user),
):
    filename = (file.filename or "unnamed").strip()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的格式: {ext or '(无扩展名)'}。支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    raw = await file.read()
    size = len(raw)
    if size == 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{size / 1024 / 1024:.1f} MB），上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
        )

    # 解析 + 分块 + 关键词（在线程池里跑，不阻塞事件循环）
    try:
        parsed = await asyncio.to_thread(_parse_and_chunk, filename, raw)
    except Exception as exc:
        logger.exception(f"文档解析失败: {filename}")
        raise HTTPException(status_code=400, detail=f"文档解析失败: {exc}") from exc

    chunks: List[str] = parsed["chunks"]
    keywords: List[str] = parsed["keywords"]
    domain_ids_list = _split_domains(domain_ids)

    # 1) 先登记元数据，拿到稳定的 doc_id
    record = knowledge_registry.register_document(
        filename=filename,
        raw_bytes=raw,
        size_bytes=size,
        chunks=len(chunks),
        keywords=keywords,
        domains=domain_ids_list,
        vector_status="pending",
        vector_message="",
        uploaded_by=str(current_user.get("user_id") or ""),
    )
    doc_id = record["doc_id"]

    # 2) 再向量化（用 doc_id 作分块 ID 前缀，避免同名文件冲突）
    vector_status, vector_message = await _maybe_vectorize(
        doc_id=doc_id,
        doc_name=filename,
        chunks=chunks,
        domain_ids=domain_ids_list,
    )

    # 3) 用真实状态覆盖 pending
    updated = knowledge_registry.update_vector_status(
        doc_id, status=vector_status, message=vector_message
    )
    return ok(updated or record)


def _split_domains(raw: str) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_and_chunk(filename: str, raw: bytes) -> dict:
    """写临时文件 → 调用 viral_agent 的 DocumentParser → 返回 chunks + keywords。"""
    import tempfile
    from pathlib import Path

    from viral_agent.services.knowledge.document_parser import DocumentParser

    suffix = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt")
    parser = DocumentParser()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(raw)
        tmp_path = tf.name

    try:
        parsed = parser.parse_file(tmp_path)
        text = parsed.get("text") or ""
        chunks = parser.split_text(text, chunk_size=500, overlap=50) if text else []
        try:
            keywords = parser.extract_keywords(text, top_k=10) if text else []
        except Exception:
            keywords = []
        return {"chunks": chunks, "keywords": keywords}
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


async def _maybe_vectorize(
    *,
    doc_id: str,
    doc_name: str,
    chunks: List[str],
    domain_ids: List[str],
) -> tuple[str, str]:
    """尝试写入 pgvector；失败或依赖缺失时返回 skipped，不抛异常阻塞上传。"""
    if not chunks:
        return "skipped", "文档文本为空，无可向量化内容"

    try:
        import os

        has_embedding = bool(
            os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        )
        if not has_embedding:
            return "skipped", "未配置 EMBEDDING_API_KEY/OPENAI_API_KEY，跳过向量化"
    except Exception:
        return "skipped", "向量化配置检查异常"

    def _do():
        from viral_agent.services.knowledge.rag_service import RAGService

        rag = RAGService()
        embeddings = rag._get_embeddings(chunks)
        base_metadata = {
            "source": doc_name,
            "doc_id": doc_id,
            "title": doc_name,
            "domains": ",".join(domain_ids),
        }
        return rag.upsert_chunks(
            doc_id=doc_id,
            title=doc_name,
            chunks=chunks,
            domains=domain_ids,
            embeddings=embeddings,
            base_metadata=base_metadata,
        )

    try:
        count = await asyncio.to_thread(_do)
        return "indexed", f"已向量化并入库 {count} 个分块"
    except Exception as exc:
        logger.warning(f"向量化失败，降级为元数据登记: {exc}")
        return "skipped", f"向量化失败: {exc}"


# ---------------------------------------------------------------------------
# 查看分块 / 检索测试
# ---------------------------------------------------------------------------
@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(
    doc_id: str,
    limit: int = 200,
    current_user: dict = Depends(require_admin_user),
):
    """
    返回文档的分块列表（仅管理员可见）。
    - 已向量化：从 pgvector 按 doc_id 过滤读取，返回分块文本 + 元数据
    - 未向量化：回落到磁盘文件，用 document_parser 现场重新解析分块
    - 两种来源返回结构一致，前端无需区分
    """
    _ = current_user
    record = knowledge_registry.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    chunks: List[dict] = []
    source = "reparsed"

    # 优先从 pgvector 取
    if record.get("vector_status") == "indexed":
        try:
            chunks = await asyncio.to_thread(_fetch_chunks_from_pgvector, doc_id, limit)
            if chunks:
                source = "pgvector"
        except Exception as exc:
            logger.warning(f"从 pgvector 读取分块失败，回落到重解析: {exc}")

    # Fallback：重新解析本地文件
    if not chunks:
        stored = knowledge_registry.absolute_stored_path(record)
        if not stored:
            raise HTTPException(
                status_code=410,
                detail="原始文件已丢失，无法查看分块；向量化数据也为空",
            )
        chunks = await asyncio.to_thread(_reparse_chunks, str(stored), limit)

    return ok(
        {
            "doc_id": doc_id,
            "name": record.get("name"),
            "format": record.get("format"),
            "source": source,
            "total": len(chunks),
            "chunks": chunks,
        }
    )


class ChunkSearchPayload(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_id: Optional[str] = None


@router.post("/search")
async def search_chunks(
    payload: ChunkSearchPayload,
    current_user: dict = Depends(require_admin_user),
):
    """
    RAG 检索测试：返回与 query 最相似的 top_k 分块（仅管理员可用）。
    要求 pgvector 有数据且 embedding 可用；否则返回 503。
    """
    _ = current_user
    try:
        result = await asyncio.to_thread(
            _pgvector_similarity_query, payload.query, payload.top_k, payload.doc_id
        )
    except Exception as exc:
        logger.warning(f"RAG 检索异常: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"RAG 检索不可用：{exc}。请确认已配置 EMBEDDING_API_KEY 并成功向量化过文档。",
        ) from exc
    return ok({"query": payload.query, "top_k": payload.top_k, **result})


# ---------------------------------------------------------------------------
# 内部工具：pgvector / 文件重解析
# ---------------------------------------------------------------------------
def _fetch_chunks_from_pgvector(doc_id: str, limit: int) -> List[dict]:
    from sqlalchemy import text as _sql

    with get_business_db_session() as session:
        rows = session.execute(
            _sql(
                """
                SELECT chunk_id, chunk_index, text, metadata
                FROM knowledge_chunks
                WHERE doc_id = :doc_id
                ORDER BY chunk_index ASC
                LIMIT :limit
                """
            ),
            {"doc_id": doc_id, "limit": limit},
        ).mappings().all()

    return [
        {
            "chunk_id": row.get("chunk_id"),
            "chunk_index": row.get("chunk_index"),
            "text": row.get("text") or "",
            "metadata": dict(row.get("metadata") or {}),
        }
        for row in rows
    ]


def _reparse_chunks(stored_path: str, limit: int) -> List[dict]:
    from viral_agent.services.knowledge.document_parser import DocumentParser

    parser = DocumentParser()
    parsed = parser.parse_file(stored_path)
    text = parsed.get("text") or ""
    chunks = parser.split_text(text, chunk_size=500, overlap=50) if text else []

    items: List[dict] = []
    for idx, chunk in enumerate(chunks[:limit]):
        items.append(
            {
                "chunk_id": f"reparsed:{idx}",
                "chunk_index": idx,
                "text": chunk,
                "metadata": {"source": "reparsed"},
            }
        )
    return items


def _pgvector_similarity_query(query: str, top_k: int, doc_id: Optional[str]) -> dict:
    from viral_agent.services.knowledge.rag_service import RAGService

    rag = RAGService()
    results = rag.search(query, None, top_k=top_k, min_score=0.0)
    if doc_id:
        results = [item for item in results if item.doc_id == doc_id]
    hits = []
    for item in results[:top_k]:
        hits.append(
            {
                "chunk_id": f"{item.doc_id}:chunk:{item.chunk_index}",
                "chunk_index": item.chunk_index,
                "text": item.text,
                "metadata": item.metadata,
                "distance": None,
                "similarity": item.score,
            }
        )
    return {"hits": hits, "total": len(hits)}
