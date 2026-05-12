"""
知识库 API：
- /documents：文档列表 / 上传（multipart） / 删除
  · 上传时调用 viral_agent.services.knowledge.document_parser 解析 + 分块
  · 可选向量化：如果 Embedding + pgvector 可用则自动入库，否则跳过不阻塞

领域知识（domains）已于 2026-05 下线（B 方案）：路由层不再暴露 /domains CRUD，
相关 DB 表与 domains 字段保留作为历史数据，待 Phase 5 一并清理。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from ...core.responses import ok
from ...core.security import RoleLevel, get_current_user, require_admin_user, role_allows
from ...infrastructure.db.engine import get_business_db_session
from ...services.knowledge_registry import knowledge_registry


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "md", "markdown", "txt"}


def _user_id(current_user: dict) -> str:
    return str(current_user.get("user_id") or "")


def _is_admin(current_user: dict) -> bool:
    return role_allows(current_user.get("role"), RoleLevel.admin)


def _require_min_role(current_user: dict, minimum: RoleLevel) -> None:
    if not role_allows(current_user.get("role"), minimum):
        raise HTTPException(status_code=403, detail="权限不足")


def _require_document_access(doc_id: str, current_user: dict) -> dict:
    record = knowledge_registry.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    if _is_admin(current_user) or record.get("owner_user_id") == _user_id(current_user):
        return record
    raise HTTPException(status_code=403, detail="无权访问此文档")


# ---------------------------------------------------------------------------
# 文档列表 / 删除
# ---------------------------------------------------------------------------
@router.get("/documents")
async def list_documents(current_user: dict = Depends(get_current_user)):
    include_all = _is_admin(current_user)
    return ok(
        {
            "items": knowledge_registry.list_documents(
                owner_user_id=_user_id(current_user),
                include_all=include_all,
            ),
            "include_all_effective": include_all,
        }
    )


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(require_admin_user),
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
    current_user: dict = Depends(get_current_user),
):
    _require_min_role(current_user, RoleLevel.analyst)
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

    # 1) 先登记元数据，拿到稳定的 doc_id（domains 字段保持空列表，schema 兼容）
    record = knowledge_registry.register_document(
        filename=filename,
        raw_bytes=raw,
        size_bytes=size,
        chunks=len(chunks),
        keywords=keywords,
        domains=[],
        vector_status="pending",
        vector_message="",
        uploaded_by=_user_id(current_user),
        owner_user_id=_user_id(current_user),
    )
    doc_id = record["doc_id"]

    # 2) 再向量化（用 doc_id 作分块 ID 前缀，避免同名文件冲突）
    vector_status, vector_message = await _maybe_vectorize(
        doc_id=doc_id,
        doc_name=filename,
        chunks=chunks,
        owner_user_id=_user_id(current_user),
    )

    # 3) 用真实状态覆盖 pending
    updated = knowledge_registry.update_vector_status(
        doc_id, status=vector_status, message=vector_message
    )
    return ok(updated or record)


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
    owner_user_id: str,
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
            "owner_user_id": owner_user_id,
        }
        return rag.upsert_chunks(
            doc_id=doc_id,
            title=doc_name,
            chunks=chunks,
            domains=[],
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
    current_user: dict = Depends(get_current_user),
):
    """
    返回文档的分块列表（仅管理员可见）。
    - 已向量化：从 pgvector 按 doc_id 过滤读取，返回分块文本 + 元数据
    - 未向量化：回落到磁盘文件，用 document_parser 现场重新解析分块
    - 两种来源返回结构一致，前端无需区分
    """
    record = _require_document_access(doc_id, current_user)

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
    current_user: dict = Depends(get_current_user),
):
    """
    RAG 检索测试：返回与 query 最相似的 top_k 分块（仅管理员可用）。
    要求 pgvector 有数据且 embedding 可用；否则返回 503。
    """
    if payload.doc_id:
        _require_document_access(payload.doc_id, current_user)
    try:
        result = await asyncio.to_thread(
            _pgvector_similarity_query,
            payload.query,
            payload.top_k,
            payload.doc_id,
            current_user,
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


def _format_pgvector(vector: List[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def _pgvector_similarity_query(
    query: str,
    top_k: int,
    doc_id: Optional[str],
    current_user: dict,
) -> dict:
    from sqlalchemy import text as _sql
    from viral_agent.services.knowledge.rag_service import RAGService

    rag = RAGService()
    query_embedding = rag._get_embeddings([query])[0]
    params = {
        "embedding": _format_pgvector(query_embedding),
        "embedding_model": rag.embedding_model,
        "top_k": top_k,
        "owner_user_id": _user_id(current_user),
    }
    where = [
        "c.embedding IS NOT NULL",
        "c.embedding_model = :embedding_model",
    ]
    if not _is_admin(current_user):
        where.append("d.owner_user_id = :owner_user_id")
    if doc_id:
        where.append("c.doc_id = :doc_id")
        params["doc_id"] = doc_id
    where_sql = " AND ".join(where)
    with get_business_db_session() as session:
        rows = session.execute(
            _sql(
                f"""
                SELECT c.doc_id, c.chunk_index, c.text, c.metadata,
                       1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.doc_id = c.doc_id
                WHERE {where_sql}
                ORDER BY c.embedding <=> CAST(:embedding AS vector)
                LIMIT :top_k
                """
            ),
            params,
        ).mappings().all()
    hits = []
    for row in rows:
        hits.append(
            {
                "chunk_id": f"{row['doc_id']}:chunk:{row['chunk_index']}",
                "chunk_index": int(row["chunk_index"]),
                "text": str(row["text"] or ""),
                "metadata": dict(row["metadata"] or {}),
                "distance": None,
                "similarity": round(float(row["score"] or 0), 4),
            }
        )
    return {"hits": hits, "total": len(hits)}
