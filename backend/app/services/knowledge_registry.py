"""
KnowledgeRegistry：知识库文档元数据的文件级持久化。

阶段3：JSON 文件存元数据、二进制文件落盘 `datas/knowledge/files/`。
阶段4 将迁移到 PostgreSQL + 对象存储。

领域知识（domains）已于 2026-05 下线（B 方案）：registry 不再提供领域 CRUD。
文档记录中的 ``domains`` 字段仅作为空列表保留以兼容历史 schema，待 Phase 5 一并清理。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from loguru import logger

from ..infrastructure.db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _JsonStore:
    """极简线程安全 JSON 字典存储。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"知识库存储解析失败 {self.path}: {exc}")
                return {}

    def save(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class KnowledgeRegistry:
    """文档（documents）+ 文件落盘的统一入口。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.root = root or (repo_root / "datas" / "knowledge")
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(exist_ok=True)

        self._documents = _JsonStore(self.root / "documents.json")

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------
    def list_documents(
        self,
        *,
        owner_user_id: Optional[str] = None,
        include_all: bool = False,
    ) -> List[Dict[str, Any]]:
        data = self._documents.load()
        items = []
        for record in data.values():
            item = dict(record)
            item["owner_user_id"] = item.get("owner_user_id") or item.get("uploaded_by") or "admin"
            if not include_all and item.get("owner_user_id") != owner_user_id:
                continue
            items.append(item)
        items.sort(key=lambda d: d.get("uploaded_at") or "", reverse=True)
        return items

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        record = self._documents.load().get(doc_id)
        if not record:
            return None
        item = dict(record)
        item["owner_user_id"] = item.get("owner_user_id") or item.get("uploaded_by") or "admin"
        return item

    def register_document(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        size_bytes: int,
        chunks: int,
        keywords: List[str],
        domains: List[str],
        vector_status: str,
        vector_message: str = "",
        uploaded_by: str = "",
        owner_user_id: str = "",
    ) -> Dict[str, Any]:
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        ext = Path(filename).suffix.lower().lstrip(".") or "txt"
        stored_name = f"{doc_id}.{ext}"
        stored_path = self.files_dir / stored_name
        stored_path.write_bytes(raw_bytes)

        record: Dict[str, Any] = {
            "doc_id": doc_id,
            "name": filename,
            "format": ext,
            "size_bytes": size_bytes,
            "chunks": chunks,
            "keywords": keywords,
            "domains": domains,
            "stored_path": str(stored_path.relative_to(self.root)),
            "vector_status": vector_status,
            "vector_message": vector_message,
            "uploaded_by": uploaded_by,
            "owner_user_id": owner_user_id or uploaded_by or "admin",
            "uploaded_at": _utc_now(),
        }
        data = self._documents.load()
        data[doc_id] = record
        self._documents.save(data)
        return record

    def update_vector_status(
        self,
        doc_id: str,
        *,
        status: str,
        message: str = "",
    ) -> Optional[Dict[str, Any]]:
        data = self._documents.load()
        record = data.get(doc_id)
        if not record:
            return None
        record["vector_status"] = status
        record["vector_message"] = message
        data[doc_id] = record
        self._documents.save(data)
        return record

    def absolute_stored_path(self, record: Dict[str, Any]) -> Optional[Path]:
        rel = record.get("stored_path")
        if not rel:
            return None
        abs_path = self.root / rel
        return abs_path if abs_path.exists() else None

    def delete_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        data = self._documents.load()
        record = data.get(doc_id)
        if not record:
            return None
        data.pop(doc_id)
        self._documents.save(data)
        # 同步删除物理文件
        try:
            stored = self.root / record.get("stored_path", "")
            if stored.exists() and stored.is_file():
                stored.unlink()
        except Exception as exc:
            logger.warning(f"删除文档物理文件失败 {doc_id}: {exc}")
        return record


class SqlAlchemyKnowledgeRegistry(KnowledgeRegistry):
    """PostgreSQL-backed knowledge metadata registry.

    Uploaded binary files still live under ``datas/knowledge/files``.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.root = root or (repo_root / "datas" / "knowledge")
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(exist_ok=True)

    def list_documents(
        self,
        *,
        owner_user_id: Optional[str] = None,
        include_all: bool = False,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        where_sql = ""
        if not include_all:
            where_sql = "WHERE owner_user_id = :owner_user_id"
            params["owner_user_id"] = owner_user_id or ""
        with get_business_db_session() as session:
            rows = session.execute(
                text(
                    f"SELECT * FROM knowledge_documents {where_sql} ORDER BY uploaded_at DESC"
                ),
                params,
            ).mappings().all()
        return [self._document_from_row(row) for row in rows]

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT * FROM knowledge_documents WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            ).mappings().first()
        return self._document_from_row(row) if row else None

    def register_document(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        size_bytes: int,
        chunks: int,
        keywords: List[str],
        domains: List[str],
        vector_status: str,
        vector_message: str = "",
        uploaded_by: str = "",
        owner_user_id: str = "",
    ) -> Dict[str, Any]:
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        ext = Path(filename).suffix.lower().lstrip(".") or "txt"
        stored_name = f"{doc_id}.{ext}"
        stored_path = self.files_dir / stored_name
        stored_path.write_bytes(raw_bytes)
        record = {
            "doc_id": doc_id,
            "name": filename,
            "format": ext,
            "size_bytes": size_bytes,
            "chunks": chunks,
            "keywords": keywords,
            "domains": domains,
            "stored_path": str(stored_path.relative_to(self.root)),
            "vector_status": vector_status,
            "vector_message": vector_message,
            "uploaded_by": uploaded_by,
            "owner_user_id": owner_user_id or uploaded_by or "admin",
            "uploaded_at": _utc_now(),
            "metadata": {},
        }
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO knowledge_documents(
                        doc_id, name, format, size_bytes, chunks, keywords, domains,
                        stored_path, vector_status, vector_message, uploaded_by,
                        owner_user_id, uploaded_at, metadata
                    ) VALUES (
                        :doc_id, :name, :format, :size_bytes, :chunks,
                        CAST(:keywords AS jsonb), CAST(:domains AS jsonb), :stored_path,
                        :vector_status, :vector_message, :uploaded_by, :owner_user_id,
                        CAST(:uploaded_at AS timestamptz), CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    **record,
                    "keywords": json.dumps(keywords, ensure_ascii=False),
                    "domains": json.dumps(domains, ensure_ascii=False),
                    "metadata": json.dumps({}, ensure_ascii=False),
                },
            )
        return record

    def update_vector_status(
        self,
        doc_id: str,
        *,
        status: str,
        message: str = "",
    ) -> Optional[Dict[str, Any]]:
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    UPDATE knowledge_documents
                    SET vector_status = :status, vector_message = :message
                    WHERE doc_id = :doc_id
                    """
                ),
                {"doc_id": doc_id, "status": status, "message": message},
            )
        return self.get_document(doc_id)

    def delete_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        record = self.get_document(doc_id)
        if not record:
            return None
        with get_business_db_session() as session:
            session.execute(
                text("DELETE FROM knowledge_documents WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
        try:
            stored = self.root / record.get("stored_path", "")
            if stored.exists() and stored.is_file():
                stored.unlink()
        except Exception as exc:
            logger.warning(f"删除文档物理文件失败 {doc_id}: {exc}")
        return record

    @staticmethod
    def _iso(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value or _utc_now())

    @classmethod
    def _document_from_row(cls, row: Any) -> Dict[str, Any]:
        data = dict(row)
        data["keywords"] = list(data.get("keywords") or [])
        data["domains"] = list(data.get("domains") or [])
        data["owner_user_id"] = data.get("owner_user_id") or data.get("uploaded_by") or "admin"
        data["uploaded_at"] = cls._iso(data.get("uploaded_at"))
        return data


knowledge_registry = SqlAlchemyKnowledgeRegistry()
