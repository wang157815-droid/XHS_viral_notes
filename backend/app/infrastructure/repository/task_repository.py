"""
TaskRepository：任务记录的存储抽象。

阶段2 实现：内存 + JSON 落盘（datas/tasks/*.json）。
阶段3 切 PostgreSQL。

字段：
- task_id, owner_user_id, status, input_spec
- idempotency_key, context_version, canvas_version
- created_at, updated_at, keywords, progress
- error_code, last_error
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from ...domain.task_status import TaskStatus
from ..db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or _utc_now())


@dataclass
class TaskRecord:
    task_id: str
    owner_user_id: str
    status: TaskStatus = TaskStatus.PENDING
    input_spec: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: Optional[str] = None
    context_version: int = 0
    canvas_version: int = 0
    keywords: List[str] = field(default_factory=list)
    progress: int = 0
    collected_count: int = 0
    duration_seconds: int = 0
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    error_code: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TaskRecord":
        status_value = raw.get("status") or TaskStatus.PENDING.value
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.PENDING
        return cls(
            task_id=str(raw["task_id"]),
            owner_user_id=str(raw.get("owner_user_id", "")),
            status=status,
            input_spec=raw.get("input_spec") or {},
            idempotency_key=raw.get("idempotency_key"),
            context_version=int(raw.get("context_version", 0)),
            canvas_version=int(raw.get("canvas_version", 0)),
            keywords=list(raw.get("keywords") or []),
            progress=int(raw.get("progress", 0)),
            collected_count=int(raw.get("collected_count", 0)),
            duration_seconds=int(raw.get("duration_seconds", 0)),
            created_at=raw.get("created_at") or _utc_now(),
            updated_at=raw.get("updated_at") or _utc_now(),
            error_code=raw.get("error_code"),
            last_error=raw.get("last_error"),
        )


class TaskRepository:
    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        self._dir = storage_dir or (repo_root / "datas" / "tasks")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, TaskRecord] = {}
        self._lock = RLock()
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                record = TaskRecord.from_dict(raw)
                self._records[record.task_id] = record
            except Exception as exc:
                logger.warning(f"任务记录加载失败 {path}: {exc}")

    def _persist(self, record: TaskRecord) -> None:
        path = self._dir / f"{record.task_id}.json"
        try:
            path.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"任务持久化失败 {record.task_id}: {exc}")

    def create(self, record: TaskRecord) -> TaskRecord:
        with self._lock:
            if record.task_id in self._records:
                raise ValueError(f"task_id 已存在: {record.task_id}")
            record.created_at = record.created_at or _utc_now()
            record.updated_at = record.updated_at or record.created_at
            self._records[record.task_id] = record
            self._persist(record)
            return record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._records.get(task_id)

    def require(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        if not record:
            raise KeyError(f"任务不存在: {task_id}")
        return record

    def find_by_idempotency(self, key: str) -> Optional[TaskRecord]:
        if not key:
            return None
        with self._lock:
            for record in self._records.values():
                if record.idempotency_key == key:
                    return record
            return None

    def list_for_user(
        self,
        user_id: str,
        *,
        include_all: bool = False,
        limit: int = 50,
    ) -> List[TaskRecord]:
        with self._lock:
            records = list(self._records.values())
        if not include_all:
            records = [r for r in records if r.owner_user_id == user_id]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[:limit]

    def list_for_owners(
        self,
        owner_ids: Set[str],
        *,
        limit: int = 50,
    ) -> List[TaskRecord]:
        """按多个 owner_user_id 并集列任务（国内/国际小红书号不同 id 时合并列表）。"""
        with self._lock:
            records = list(self._records.values())
        records = [r for r in records if r.owner_user_id in owner_ids]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[:limit]

    def update(self, task_id: str, **changes: Any) -> TaskRecord:
        with self._lock:
            record = self.require(task_id)
            for key, value in changes.items():
                if not hasattr(record, key):
                    continue
                if key == "status" and not isinstance(value, TaskStatus):
                    value = TaskStatus(value)
                setattr(record, key, value)
            record.updated_at = _utc_now()
            self._persist(record)
            return record

    def bump_canvas_version(self, task_id: str) -> int:
        with self._lock:
            record = self.require(task_id)
            record.canvas_version += 1
            record.updated_at = _utc_now()
            self._persist(record)
            return record.canvas_version

    def bump_context_version(self, task_id: str, to_version: int) -> None:
        with self._lock:
            record = self.require(task_id)
            if to_version > record.context_version:
                record.context_version = to_version
                record.updated_at = _utc_now()
                self._persist(record)


class SqlAlchemyTaskRepository:
    """PostgreSQL-backed TaskRepository with the same public contract."""

    def create(self, record: TaskRecord) -> TaskRecord:
        record.created_at = record.created_at or _utc_now()
        record.updated_at = record.updated_at or record.created_at
        try:
            with get_business_db_session() as session:
                exists = session.execute(
                    text("SELECT 1 FROM tasks WHERE task_id = :task_id"),
                    {"task_id": record.task_id},
                ).first()
                if exists:
                    raise ValueError(f"task_id 已存在: {record.task_id}")
                session.execute(
                    text(
                        """
                        INSERT INTO tasks (
                            task_id, owner_user_id, status, input_spec, idempotency_key,
                            context_version, canvas_version, keywords, progress,
                            collected_count, duration_seconds, created_at, updated_at,
                            error_code, last_error
                        ) VALUES (
                            :task_id, :owner_user_id, :status, CAST(:input_spec AS jsonb),
                            :idempotency_key, :context_version, :canvas_version,
                            CAST(:keywords AS jsonb), :progress, :collected_count,
                            :duration_seconds, CAST(:created_at AS timestamptz),
                            CAST(:updated_at AS timestamptz), :error_code, :last_error
                        )
                        """
                    ),
                    self._params(record),
                )
            return record
        except Exception:
            raise

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT * FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).mappings().first()
        return self._from_row(row) if row else None

    def require(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        if not record:
            raise KeyError(f"任务不存在: {task_id}")
        return record

    def find_by_idempotency(self, key: str) -> Optional[TaskRecord]:
        if not key:
            return None
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT * FROM tasks WHERE idempotency_key = :key LIMIT 1"),
                {"key": key},
            ).mappings().first()
        return self._from_row(row) if row else None

    def list_for_user(
        self,
        user_id: str,
        *,
        include_all: bool = False,
        limit: int = 50,
    ) -> List[TaskRecord]:
        if include_all:
            sql = "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT :limit"
            params = {"limit": limit}
        else:
            sql = "SELECT * FROM tasks WHERE owner_user_id = :user_id ORDER BY updated_at DESC LIMIT :limit"
            params = {"user_id": user_id, "limit": limit}
        with get_business_db_session() as session:
            rows = session.execute(text(sql), params).mappings().all()
        return [self._from_row(row) for row in rows]

    def list_for_owners(
        self,
        owner_ids: Set[str],
        *,
        limit: int = 50,
    ) -> List[TaskRecord]:
        ids = [str(item) for item in owner_ids if str(item)]
        if not ids:
            return []
        with get_business_db_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT * FROM tasks
                    WHERE owner_user_id = ANY(:owner_ids)
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"owner_ids": ids, "limit": limit},
            ).mappings().all()
        return [self._from_row(row) for row in rows]

    def update(self, task_id: str, **changes: Any) -> TaskRecord:
        allowed = {
            "owner_user_id",
            "status",
            "input_spec",
            "idempotency_key",
            "context_version",
            "canvas_version",
            "keywords",
            "progress",
            "collected_count",
            "duration_seconds",
            "error_code",
            "last_error",
        }
        assignments: List[str] = []
        params: Dict[str, Any] = {"task_id": task_id}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "status" and isinstance(value, TaskStatus):
                value = value.value
            elif key == "status":
                value = TaskStatus(value).value
            if key in {"input_spec", "keywords"}:
                assignments.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = json.dumps(value or ([] if key == "keywords" else {}), ensure_ascii=False)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        if assignments:
            assignments.append("updated_at = NOW()")
            with get_business_db_session() as session:
                session.execute(
                    text(f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = :task_id"),
                    params,
                )
        return self.require(task_id)

    def bump_canvas_version(self, task_id: str) -> int:
        with get_business_db_session() as session:
            row = session.execute(
                text(
                    """
                    UPDATE tasks
                    SET canvas_version = canvas_version + 1, updated_at = NOW()
                    WHERE task_id = :task_id
                    RETURNING canvas_version
                    """
                ),
                {"task_id": task_id},
            ).first()
        if not row:
            raise KeyError(f"任务不存在: {task_id}")
        return int(row[0])

    def bump_context_version(self, task_id: str, to_version: int) -> None:
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    UPDATE tasks
                    SET context_version = GREATEST(context_version, :to_version), updated_at = NOW()
                    WHERE task_id = :task_id AND context_version < :to_version
                    """
                ),
                {"task_id": task_id, "to_version": int(to_version)},
            )

    @staticmethod
    def _params(record: TaskRecord) -> Dict[str, Any]:
        return {
            "task_id": record.task_id,
            "owner_user_id": record.owner_user_id,
            "status": record.status.value if isinstance(record.status, TaskStatus) else str(record.status),
            "input_spec": json.dumps(record.input_spec or {}, ensure_ascii=False),
            "idempotency_key": record.idempotency_key,
            "context_version": int(record.context_version),
            "canvas_version": int(record.canvas_version),
            "keywords": json.dumps(record.keywords or [], ensure_ascii=False),
            "progress": int(record.progress),
            "collected_count": int(record.collected_count),
            "duration_seconds": int(record.duration_seconds),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "error_code": record.error_code,
            "last_error": record.last_error,
        }

    @staticmethod
    def _from_row(row: Any) -> TaskRecord:
        raw = dict(row)
        raw["created_at"] = _iso(raw.get("created_at"))
        raw["updated_at"] = _iso(raw.get("updated_at"))
        raw["input_spec"] = raw.get("input_spec") or {}
        raw["keywords"] = raw.get("keywords") or []
        return TaskRecord.from_dict(raw)


task_repository = SqlAlchemyTaskRepository()
