"""
Task Ownership 与最小 RBAC。

角色：admin / user
规则：
- user：仅能读写自己的任务
- admin：可读全量，写操作进入 audit_log
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List

from fastapi import Depends, HTTPException, Path as FastAPIPath
from loguru import logger

from ...core.security import get_current_user
from ...domain.error_codes import ErrorCode, build_error
from ...infrastructure.repository import TaskRecord, task_repository
from ...services.identity_store import get_identity_store


class TaskAuditLog:
    """简易文件审计日志（阶段2 MVP，阶段3替换为 DB）。"""

    def __init__(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        self._log_path = repo_root / "datas" / "audit" / "task_access_audit.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def record(
        self,
        *,
        actor_user_id: str,
        actor_role: str,
        task_id: str,
        owner_user_id: str,
        action: str,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "task_id": task_id,
            "owner_user_id": owner_user_id,
            "action": action,
            "extra": extra or {},
        }
        with self._lock:
            try:
                import json

                self._log_path.open("a", encoding="utf-8").write(
                    json.dumps(payload, ensure_ascii=False) + "\n"
                )
            except Exception as exc:
                logger.warning(f"审计日志写入失败: {exc}")


task_audit_log = TaskAuditLog()
_identity_store = get_identity_store()


def _is_admin(user: Dict[str, Any]) -> bool:
    return (user or {}).get("role") == "admin"


def resolve_task_record(
    task_id: str,
    current_user: Dict[str, Any],
    *,
    action: str,
    write: bool = False,
) -> TaskRecord:
    """
    返回任务记录，若当前用户无权访问则抛统一错误码。

    action：权限校验动作描述（用于审计）。
    write：True 时进入审计（管理员越权读写也会记录）。
    """
    record = task_repository.get(task_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND,
                f"任务不存在: {task_id}",
                details={"task_id": task_id},
            )["error"],
        )

    actor_id = str((current_user or {}).get("user_id") or "")
    actor_role = str((current_user or {}).get("role") or "user")
    visible = _identity_store.expand_visible_user_ids(actor_id) if actor_id else set()
    is_owner = bool(actor_id) and record.owner_user_id in visible
    is_admin = _is_admin(current_user)

    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=403,
            detail=build_error(
                ErrorCode.AUTH_FORBIDDEN,
                "无权访问此任务",
                details={"task_id": task_id, "action": action},
            )["error"],
        )

    if not is_owner and is_admin:
        task_audit_log.record(
            actor_user_id=actor_id,
            actor_role=actor_role,
            task_id=task_id,
            owner_user_id=record.owner_user_id,
            action=action,
            extra={"admin_bypass": True, "write": write},
        )

    return record


def require_task_access(
    task_id: str = FastAPIPath(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> TaskRecord:
    """FastAPI Dependency：默认为"读权限"，写操作请显式调用 resolve_task_record(write=True)。"""
    return resolve_task_record(task_id, current_user, action="read", write=False)


def list_visible_tasks(current_user: Dict[str, Any], *, include_all: bool, limit: int = 50) -> List[TaskRecord]:
    actor_id = str((current_user or {}).get("user_id") or "")
    effective_include_all = include_all and _is_admin(current_user)
    if include_all and not _is_admin(current_user):
        logger.warning("非管理员尝试 include_all=true，已忽略")
    if effective_include_all:
        return task_repository.list_for_user(actor_id, include_all=True, limit=limit)
    owner_ids = _identity_store.expand_visible_user_ids(actor_id)
    if not owner_ids:
        return []
    return task_repository.list_for_owners(owner_ids, limit=limit)
