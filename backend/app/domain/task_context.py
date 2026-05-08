"""
TaskContext：编排单一事实源（SSOT）。

约定：
- 每个 Agent 节点仅写自己的分区（crawler_output / semantic_output / ...）。
- 通过 writer.write(partition, data) 写入，触发版本自增与审计。
- 禁止节点间直接传递引用，全部通过 context 读。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from loguru import logger


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_VALID_PARTITIONS = frozenset(
    {
        "input_spec",
        "crawler_output",
        "semantic_output",
        "rag_output",
        "multimodal_output",
        "strategy_output",
        "viral_model_output",  # 4.3pre.2: ViralModelAgent 产出的爆文模型矩阵
        "canvas_document",
        "cookie_health",
        "metrics",
        "xhs_auth_status",  # Phase 2-B: XhsAuthAgent 写入凭据校验结果
    }
)


@dataclass
class ContextAuditEntry:
    version: int
    partition: str
    agent_id: str
    timestamp: str
    note: str = ""


@dataclass
class TaskContext:
    task_id: str
    context_version: int = 0
    data: Dict[str, Any] = field(default_factory=dict)
    audit_log: List[ContextAuditEntry] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def get(self, partition: str) -> Any:
        return self.data.get(partition)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "context_version": self.context_version,
            "data": dict(self.data),
            "audit_log": [asdict(entry) for entry in self.audit_log],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskContextWriter:
    """负责受控写入与版本管理，节点统一通过此类写入。"""

    def __init__(self, context: TaskContext) -> None:
        self._context = context
        self._lock = RLock()

    @property
    def context(self) -> TaskContext:
        return self._context

    def write(
        self,
        partition: str,
        data: Any,
        *,
        agent_id: str,
        merge: bool = False,
        note: str = "",
    ) -> int:
        if partition not in _VALID_PARTITIONS:
            raise ValueError(f"未知 TaskContext 分区: {partition}")
        with self._lock:
            self._context.context_version += 1
            if merge and isinstance(self._context.data.get(partition), dict) and isinstance(data, dict):
                merged = dict(self._context.data.get(partition) or {})
                merged.update(data)
                self._context.data[partition] = merged
            else:
                self._context.data[partition] = data
            self._context.updated_at = datetime.now(timezone.utc).isoformat()
            self._context.audit_log.append(
                ContextAuditEntry(
                    version=self._context.context_version,
                    partition=partition,
                    agent_id=agent_id,
                    timestamp=self._context.updated_at,
                    note=note,
                )
            )
            version = self._context.context_version
        # 写入完成后自动持久化（阶段2 JSON 落盘）
        try:
            task_context_store.persist(self._context.task_id)
        except Exception:
            pass
        return version


class TaskContextStore:
    """阶段2：内存 + JSON 落盘的简易存储；阶段3切到 DB。"""

    def __init__(self) -> None:
        self._contexts: Dict[str, TaskContext] = {}
        self._lock = RLock()
        self._load_from_disk()

    def _storage_dir(self) -> "Path":
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        d = repo_root / "datas" / "task_contexts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist(self, ctx: TaskContext) -> None:
        from pathlib import Path
        import json
        path = self._storage_dir() / f"{ctx.task_id}.json"
        try:
            path.write_text(
                json.dumps(ctx.snapshot(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"TaskContext 持久化失败 {ctx.task_id}: {exc}")

    def _load_from_disk(self) -> None:
        import json
        from pathlib import Path
        for path in self._storage_dir().glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                ctx = TaskContext(
                    task_id=str(raw["task_id"]),
                    context_version=int(raw.get("context_version", 0)),
                    data=dict(raw.get("data") or {}),
                    audit_log=[
                        ContextAuditEntry(**entry)
                        for entry in (raw.get("audit_log") or [])
                        if isinstance(entry, dict)
                    ],
                    created_at=raw.get("created_at") or _utc_now(),
                    updated_at=raw.get("updated_at") or _utc_now(),
                )
                self._contexts[ctx.task_id] = ctx
            except Exception as exc:
                logger.warning(f"TaskContext 加载失败 {path}: {exc}")

    def create(self, task_id: str, input_spec: Optional[Dict[str, Any]] = None) -> TaskContext:
        with self._lock:
            ctx = TaskContext(task_id=task_id)
            self._contexts[task_id] = ctx
            if input_spec is not None:
                TaskContextWriter(ctx).write(
                    "input_spec", input_spec, agent_id="system", note="initial"
                )
            self._persist(ctx)
            return ctx

    def get(self, task_id: str) -> Optional[TaskContext]:
        with self._lock:
            return self._contexts.get(task_id)

    def require(self, task_id: str) -> TaskContext:
        ctx = self.get(task_id)
        if not ctx:
            raise KeyError(f"TaskContext 不存在: {task_id}")
        return ctx

    def writer(self, task_id: str) -> TaskContextWriter:
        return TaskContextWriter(self.require(task_id))

    def drop(self, task_id: str) -> None:
        with self._lock:
            self._contexts.pop(task_id, None)
        from pathlib import Path
        path = self._storage_dir() / f"{task_id}.json"
        if path.exists():
            try:
                path.unlink()
            except Exception as exc:
                logger.warning(f"TaskContext 删除失败 {task_id}: {exc}")

    def persist(self, task_id: str) -> None:
        """显式触发持久化（供外部在关键写入后调用）。"""
        with self._lock:
            ctx = self._contexts.get(task_id)
            if ctx:
                self._persist(ctx)


task_context_store = TaskContextStore()
