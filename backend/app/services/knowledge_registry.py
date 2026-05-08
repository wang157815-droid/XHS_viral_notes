"""
KnowledgeRegistry：知识库领域 + 文档元数据的文件级持久化。

阶段3：JSON 文件存元数据、二进制文件落盘 `datas/knowledge/files/`。
阶段4 将迁移到 PostgreSQL + 对象存储。
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
    """领域（domains）+ 文档（documents）+ 文件落盘的统一入口。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.root = root or (repo_root / "datas" / "knowledge")
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(exist_ok=True)

        self._domains = _JsonStore(self.root / "domains.json")
        self._documents = _JsonStore(self.root / "documents.json")

    # ------------------------------------------------------------------
    # 领域
    # ------------------------------------------------------------------
    def list_domains(self) -> List[Dict[str, Any]]:
        data = self._domains.load()
        items = list(data.values())
        items.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
        return items

    def create_domain(
        self,
        *,
        name: str,
        keywords: List[str],
        priority: str = "medium",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        data = self._domains.load()
        domain_id = f"domain_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        record: Dict[str, Any] = {
            "domain_id": domain_id,
            "name": name,
            "keywords": keywords,
            "priority": priority,
            "enabled": enabled,
            "rule_count": len(keywords),
            "created_at": now,
            "updated_at": now,
        }
        data[domain_id] = record
        self._domains.save(data)
        return record

    def update_domain(
        self,
        domain_id: str,
        *,
        name: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        priority: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        data = self._domains.load()
        record = data.get(domain_id)
        if not record:
            return None
        if name is not None:
            record["name"] = name
        if keywords is not None:
            record["keywords"] = keywords
            record["rule_count"] = len(keywords)
        if priority is not None:
            record["priority"] = priority
        if enabled is not None:
            record["enabled"] = enabled
        record["updated_at"] = _utc_now()
        data[domain_id] = record
        self._domains.save(data)
        return record

    def delete_domain(self, domain_id: str) -> bool:
        data = self._domains.load()
        if domain_id not in data:
            return False
        data.pop(domain_id)
        self._domains.save(data)
        return True

    # ------------------------------------------------------------------
    # 文档
    # ------------------------------------------------------------------
    def list_documents(self, *, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._documents.load()
        items = list(data.values())
        if domain_id:
            items = [d for d in items if domain_id in (d.get("domains") or [])]
        items.sort(key=lambda d: d.get("uploaded_at") or "", reverse=True)
        return items

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self._documents.load().get(doc_id)

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

    def list_domains(self) -> List[Dict[str, Any]]:
        with get_business_db_session() as session:
            rows = session.execute(
                text("SELECT * FROM knowledge_domains ORDER BY updated_at DESC")
            ).mappings().all()
        return [self._domain_from_row(row) for row in rows]

    def create_domain(
        self,
        *,
        name: str,
        keywords: List[str],
        priority: str = "medium",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        domain_id = f"domain_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        record = {
            "domain_id": domain_id,
            "name": name,
            "keywords": keywords,
            "priority": priority,
            "enabled": enabled,
            "rule_count": len(keywords),
            "created_at": now,
            "updated_at": now,
        }
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO knowledge_domains(
                        domain_id, name, keywords, priority, enabled, rule_count, created_at, updated_at
                    ) VALUES (
                        :domain_id, :name, CAST(:keywords AS jsonb), :priority, :enabled,
                        :rule_count, CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
                    )
                    """
                ),
                {**record, "keywords": json.dumps(keywords, ensure_ascii=False)},
            )
        return record

    def update_domain(
        self,
        domain_id: str,
        *,
        name: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        priority: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        current = next((d for d in self.list_domains() if d.get("domain_id") == domain_id), None)
        if not current:
            return None
        if name is not None:
            current["name"] = name
        if keywords is not None:
            current["keywords"] = keywords
            current["rule_count"] = len(keywords)
        if priority is not None:
            current["priority"] = priority
        if enabled is not None:
            current["enabled"] = enabled
        current["updated_at"] = _utc_now()
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    UPDATE knowledge_domains SET
                        name = :name,
                        keywords = CAST(:keywords AS jsonb),
                        priority = :priority,
                        enabled = :enabled,
                        rule_count = :rule_count,
                        updated_at = CAST(:updated_at AS timestamptz)
                    WHERE domain_id = :domain_id
                    """
                ),
                {**current, "keywords": json.dumps(current.get("keywords") or [], ensure_ascii=False)},
            )
        return current

    def delete_domain(self, domain_id: str) -> bool:
        with get_business_db_session() as session:
            result = session.execute(
                text("DELETE FROM knowledge_domains WHERE domain_id = :domain_id"),
                {"domain_id": domain_id},
            )
        return bool(result.rowcount)

    def list_documents(self, *, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM knowledge_documents ORDER BY uploaded_at DESC"
        params: Dict[str, Any] = {}
        if domain_id:
            sql = """
                SELECT * FROM knowledge_documents
                WHERE domains @> CAST(:domain_filter AS jsonb)
                ORDER BY uploaded_at DESC
            """
            params["domain_filter"] = json.dumps([domain_id], ensure_ascii=False)
        with get_business_db_session() as session:
            rows = session.execute(text(sql), params).mappings().all()
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
            "uploaded_at": _utc_now(),
            "metadata": {},
        }
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO knowledge_documents(
                        doc_id, name, format, size_bytes, chunks, keywords, domains,
                        stored_path, vector_status, vector_message, uploaded_by, uploaded_at, metadata
                    ) VALUES (
                        :doc_id, :name, :format, :size_bytes, :chunks,
                        CAST(:keywords AS jsonb), CAST(:domains AS jsonb), :stored_path,
                        :vector_status, :vector_message, :uploaded_by,
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
    def _domain_from_row(cls, row: Any) -> Dict[str, Any]:
        data = dict(row)
        data["keywords"] = list(data.get("keywords") or [])
        data["created_at"] = cls._iso(data.get("created_at"))
        data["updated_at"] = cls._iso(data.get("updated_at"))
        return data

    @classmethod
    def _document_from_row(cls, row: Any) -> Dict[str, Any]:
        data = dict(row)
        data["keywords"] = list(data.get("keywords") or [])
        data["domains"] = list(data.get("domains") or [])
        data["uploaded_at"] = cls._iso(data.get("uploaded_at"))
        return data


knowledge_registry = SqlAlchemyKnowledgeRegistry()
