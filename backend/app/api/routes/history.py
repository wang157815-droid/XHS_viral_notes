from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from ...core.responses import ok
from ...core.security import RoleLevel, get_current_user, role_allows
from ...application.task_service import task_service
from ...services.conversation_store import get_conversation_store

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/tasks")
async def list_history_tasks(
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    time_range: str = Query(default="7d"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    _ = (keyword, status, time_range)
    include_all = role_allows(current_user.get("role"), RoleLevel.admin)
    records = task_service.list_tasks(str(current_user["user_id"]), include_all=include_all, limit=500)
    items: List[Dict[str, Any]] = []
    kw = (keyword or "").strip().lower()
    for record in records:
        if status and record.status.value != status:
            continue
        search_text = " ".join([record.task_id, *(record.keywords or [])]).lower()
        if kw and kw not in search_text:
            continue
        items.append(
            {
                "task_id": record.task_id,
                "keywords": record.keywords,
                "status": record.status.value,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "progress": record.progress,
                "collected_count": record.collected_count,
                "duration_seconds": record.duration_seconds,
                "owner_user_id": record.owner_user_id,
            }
        )
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return ok(
        {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/timeline")
async def list_history_timeline(
    keyword: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=300),
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user["user_id"])
    include_all = role_allows(current_user.get("role"), RoleLevel.admin)
    kw = (keyword or "").strip().lower()
    timeline: List[Dict[str, Any]] = []

    tasks = task_service.list_tasks(user_id, include_all=include_all, limit=limit)
    for record in tasks:
        search_text = " ".join([record.task_id, *(record.keywords or [])]).lower()
        if kw and kw not in search_text:
            continue
        timeline.append(
            {
                "type": "task",
                "id": record.task_id,
                "title": " / ".join(record.keywords[:3]) or record.task_id,
                "status": record.status.value,
                "updated_at": record.updated_at,
                "created_at": record.created_at,
                "preview": f"进度 {record.progress}%",
                "task_id": record.task_id,
            }
        )

    conversations = get_conversation_store().list_for_owner(
        user_id,
        include_all=include_all,
        include_archived=include_archived,
        keyword=keyword,
        limit=limit,
    )
    for item in conversations:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if metadata.get("deleted") is True and not include_archived:
            continue
        timeline.append(
            {
                "type": "conversation",
                "id": item.get("conversation_id"),
                "title": item.get("title") or "新对话",
                "status": "archived" if metadata.get("archived") else "active",
                "updated_at": item.get("updated_at"),
                "created_at": item.get("created_at"),
                "preview": item.get("last_message_preview") or item.get("summary") or "",
                "conversation_id": item.get("conversation_id"),
                "active_task_id": item.get("active_task_id"),
                "message_count": item.get("message_count"),
            }
        )

    timeline.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return ok({"items": timeline[:limit], "total": len(timeline)})

