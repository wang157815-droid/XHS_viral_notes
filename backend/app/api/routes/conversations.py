"""Conversation OS API.

4.4 首版先提供会话和消息的持久化入口，后续应用层在同一路由下接入意图路由、
知识库问答和任务交接。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...application.conversation_service import ConversationService
from ...core.config import settings
from ...core.responses import ok
from ...core.security import get_current_user
from ...domain.error_codes import ErrorCode, build_error
from ...services.conversation_store import get_conversation_store


router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    keywords: List[str] = Field(default_factory=list)
    competitor_keywords: List[str] = Field(default_factory=list)
    advanced_config: Dict[str, Any] = Field(default_factory=dict)
    domain_ids: Optional[List[str]] = None
    active_task_id: Optional[str] = None
    client_message_id: Optional[str] = None


class UpdateConversationStateRequest(BaseModel):
    reason: Optional[str] = None


def _user_id(current_user: Dict[str, Any]) -> str:
    return str(current_user["user_id"])


def _ensure_conversation_enabled() -> None:
    if settings.conversation_os_enabled:
        return
    raise HTTPException(
        status_code=503,
        detail=build_error(
            ErrorCode.SYSTEM_DEGRADED,
            "Conversation OS 当前未启用",
            details={"flag": "CONVERSATION_OS_ENABLED"},
        )["error"],
    )


def _require_owned_conversation(conversation_id: str, current_user: Dict[str, Any]):
    store = get_conversation_store()
    try:
        return store.ensure_owner(conversation_id, _user_id(current_user))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND,
                f"会话不存在: {conversation_id}",
                details={"conversation_id": conversation_id},
            )["error"],
        )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=build_error(
                ErrorCode.AUTH_FORBIDDEN,
                "无权访问此会话",
                details={"conversation_id": conversation_id},
            )["error"],
        )


@router.post("")
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    conversation = get_conversation_store().create(
        owner_user_id=_user_id(current_user),
        title=payload.title,
        metadata=payload.metadata,
    )
    return ok(conversation.to_dict())


@router.get("")
async def list_conversations(
    include_all: bool = Query(False),
    include_archived: bool = Query(False),
    keyword: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    include_all_effective = include_all and current_user.get("role") == "admin"
    items = get_conversation_store().list_for_owner(
        _user_id(current_user),
        limit=limit,
        include_all=include_all_effective,
        include_archived=include_archived,
        keyword=keyword,
    )
    return ok({"items": items, "include_all_effective": include_all_effective})


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    result = get_conversation_store().get_with_messages(conversation_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND,
                f"会话不存在: {conversation_id}",
                details={"conversation_id": conversation_id},
            )["error"],
        )
    conversation, messages = result
    return ok(
        {
            "conversation": conversation.to_dict(),
            "messages": [message.to_dict() for message in messages],
        }
    )


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=200),
    before: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    messages, has_more = get_conversation_store().list_messages_page(
        conversation_id,
        limit=limit,
        before=before,
    )
    return ok({"items": [message.to_dict() for message in messages], "has_more": has_more})


@router.post("/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str,
    payload: UpdateConversationStateRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    conversation = get_conversation_store().update_conversation(
        conversation_id,
        metadata_patch={
            "archived": True,
            "deleted": False,
            "archive_reason": payload.reason,
        },
    )
    return ok(conversation.to_dict())


@router.post("/{conversation_id}/restore")
async def restore_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    conversation = get_conversation_store().update_conversation(
        conversation_id,
        metadata_patch={"archived": False, "deleted": False},
    )
    return ok(conversation.to_dict())


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    conversation = get_conversation_store().update_conversation(
        conversation_id,
        metadata_patch={"archived": True, "deleted": True},
    )
    return ok(conversation.to_dict())


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    service = ConversationService(store=get_conversation_store())
    result = await service.handle_user_message(
        conversation_id=conversation_id,
        owner_user_id=_user_id(current_user),
        content=payload.content,
        keywords=payload.keywords,
        competitor_keywords=payload.competitor_keywords,
        advanced_config=payload.advanced_config,
        domain_ids=payload.domain_ids,
        active_task_id=payload.active_task_id,
        client_message_id=payload.client_message_id,
        current_user=current_user,
    )
    return ok(
        {
            "user_message": result["user_message"].to_dict(),
            "assistant_message": result["assistant_message"].to_dict(),
            "conversation": result["conversation"].to_dict(),
            "intent": result["intent"].to_dict(),
        }
    )


@router.post("/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: str,
    payload: SendMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_owned_conversation(conversation_id, current_user)
    service = ConversationService(store=get_conversation_store())

    async def event_generator():
        async for event in service.handle_user_message_stream(
            conversation_id=conversation_id,
            owner_user_id=_user_id(current_user),
            content=payload.content,
            keywords=payload.keywords,
            competitor_keywords=payload.competitor_keywords,
            advanced_config=payload.advanced_config,
            domain_ids=payload.domain_ids,
            active_task_id=payload.active_task_id,
            client_message_id=payload.client_message_id,
            current_user=current_user,
        ):
            data = json.dumps(event, ensure_ascii=False)
            yield f"event: conversation\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
