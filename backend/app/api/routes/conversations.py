"""Conversation OS API.

4.4 首版先提供会话和消息的持久化入口，后续应用层在同一路由下接入意图路由、
知识库问答和任务交接。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from ...application.conversation_service import ConversationService
from ...core.config import settings
from ...core.responses import ok
from ...core.security import RoleLevel, get_current_user, role_allows
from ...domain.conversation import new_id
from ...domain.error_codes import ErrorCode, build_error
from ...domain.conversation_surfaces import parse_surfaces_query
from ...services.conversation_store import get_conversation_store


router = APIRouter(prefix="/conversations", tags=["conversations"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ALLOWED_UPLOAD_EXT = {".txt", ".md", ".markdown", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PatchConversationRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=512)


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=32000)
    keywords: List[str] = Field(default_factory=list)
    competitor_keywords: List[str] = Field(default_factory=list)
    advanced_config: Dict[str, Any] = Field(default_factory=dict)
    active_task_id: Optional[str] = None
    client_message_id: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_refs: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _content_or_attachments(self) -> "SendMessageRequest":
        if not (self.content or "").strip() and not self.attachments:
            raise ValueError("至少需要输入文字或添加附件")
        return self


class UpdateConversationStateRequest(BaseModel):
    reason: Optional[str] = None


def _user_id(current_user: Dict[str, Any]) -> str:
    return str(current_user["user_id"])


def _is_admin(current_user: Dict[str, Any]) -> bool:
    return role_allows(current_user.get("role"), RoleLevel.admin)


def _require_conversation_write_role(current_user: Dict[str, Any]) -> None:
    if not role_allows(current_user.get("role"), RoleLevel.analyst):
        raise HTTPException(status_code=403, detail="需要分析师或管理员权限")


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
        conversation = store.get(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")
        if conversation.owner_user_id == _user_id(current_user) or _is_admin(current_user):
            return conversation
        raise PermissionError("Conversation owner mismatch")
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _safe_filename(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^a-zA-Z0-9._-]", "_", base)[:180] or "upload"


@router.post("")
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_conversation_write_role(current_user)
    conversation = get_conversation_store().create(
        owner_user_id=_user_id(current_user),
        title=payload.title,
        metadata=payload.metadata,
    )
    return ok(conversation.to_dict())


@router.post("/uploads")
async def upload_conversation_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """上传对话附件（图片/文档），供发送消息时引用。"""
    _ensure_conversation_enabled()
    _require_conversation_write_role(current_user)
    uid = _user_id(current_user)
    raw_name = file.filename or "file"
    ext = Path(raw_name).suffix.lower()
    if ext and ext not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")
    body = await file.read()
    if len(body) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 10MB 限制")
    fid = new_id("att")
    safe = _safe_filename(raw_name)
    sub = f"{uid}/{fid}_{safe}"
    base = _repo_root() / "datas" / "conversation_uploads"
    dest = base / sub
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    mime = file.content_type or "application/octet-stream"

    # 立即解析文档（PDF/文本），缓存解析结果供发送消息时直接使用
    parsed_meta: Optional[Dict[str, Any]] = None
    if ext in {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx"}:
        try:
            from viral_agent.services.knowledge.document_parser import DocumentParser

            parser = DocumentParser()
            parsed = parser.parse_file(str(dest))
            parsed_cache = {
                "text": parsed.get("text") or "",
                "metadata": parsed.get("metadata") or {},
            }
            (dest.parent / f"{dest.name}.parsed.json").write_text(
                json.dumps(parsed_cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            meta = parsed.get("metadata") or {}
            parsed_meta = {
                "word_count": meta.get("word_count"),
                "pages": meta.get("pages"),
                "format": meta.get("format"),
            }
        except Exception:
            # 解析失败不阻塞上传，发送消息时回落到现场解析
            pass

    return ok(
        {
            "file_id": fid,
            "filename": raw_name,
            "mime_type": mime,
            "size_bytes": len(body),
            "storage_subpath": sub.replace("\\", "/"),
            "parsed": parsed_meta,
        }
    )


def _upload_subpath_for_user(uid: str, subpath: str) -> Path:
    sub = (subpath or "").strip().replace("\\", "/")
    if not sub or ".." in sub:
        raise HTTPException(status_code=400, detail="无效的 subpath")
    prefix = f"{uid}/"
    if not sub.startswith(prefix) or len(sub) <= len(prefix):
        raise HTTPException(status_code=403, detail="无权访问该附件")
    base = _repo_root() / "datas" / "conversation_uploads"
    path = base / sub
    if not path.is_file():
        raise HTTPException(status_code=404, detail="附件不存在")
    return path


@router.get("/uploads/content")
async def download_conversation_upload(
    subpath: str = Query(..., description="上传接口返回的 storage_subpath"),
    current_user: dict = Depends(get_current_user),
):
    """下载本人上传的对话附件（供前端带鉴权拉取预览）。"""
    _ensure_conversation_enabled()
    uid = _user_id(current_user)
    path = _upload_subpath_for_user(uid, subpath)
    mime = "application/octet-stream"
    ext = path.suffix.lower()
    guessed = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".markdown": "text/markdown; charset=utf-8",
    }
    if ext in guessed:
        mime = guessed[ext]
    return FileResponse(path, filename=path.name, media_type=mime)


@router.get("")
async def list_conversations(
    include_all: bool = Query(False),
    include_archived: bool = Query(False),
    keyword: Optional[str] = Query(None),
    surfaces: Optional[str] = Query(
        None,
        description="逗号分隔：insight,hotspot,post_investment；不传则返回全部",
    ),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    include_all_effective = include_all and role_allows(current_user.get("role"), RoleLevel.admin)
    surface_list = parse_surfaces_query(surfaces)
    items = get_conversation_store().list_for_owner(
        _user_id(current_user),
        limit=limit,
        include_all=include_all_effective,
        include_archived=include_archived,
        keyword=keyword,
        surfaces=surface_list,
    )
    return ok({"items": items, "include_all_effective": include_all_effective})


@router.patch("/{conversation_id}")
async def patch_conversation(
    conversation_id: str,
    payload: PatchConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_conversation_enabled()
    _require_conversation_write_role(current_user)
    _require_owned_conversation(conversation_id, current_user)
    if payload.title is None:
        raise HTTPException(status_code=400, detail="至少需要提供 title")
    conversation = get_conversation_store().update_conversation(
        conversation_id,
        title=payload.title,
        metadata_patch={"auto_titled": False, "manual_titled": True},
    )
    return ok(conversation.to_dict())


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
    _require_conversation_write_role(current_user)
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
    _require_conversation_write_role(current_user)
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
    _require_conversation_write_role(current_user)
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
    _require_conversation_write_role(current_user)
    conversation = _require_owned_conversation(conversation_id, current_user)
    service = ConversationService(store=get_conversation_store())
    result = await service.handle_user_message(
        conversation_id=conversation_id,
        owner_user_id=conversation.owner_user_id,
        content=payload.content,
        keywords=payload.keywords,
        competitor_keywords=payload.competitor_keywords,
        advanced_config=payload.advanced_config,
        active_task_id=payload.active_task_id,
        client_message_id=payload.client_message_id,
        current_user=current_user,
        attachments=payload.attachments,
        knowledge_refs=payload.knowledge_refs,
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
    _require_conversation_write_role(current_user)
    conversation = _require_owned_conversation(conversation_id, current_user)
    service = ConversationService(store=get_conversation_store())

    async def event_generator():
        async for event in service.handle_user_message_stream(
            conversation_id=conversation_id,
            owner_user_id=conversation.owner_user_id,
            content=payload.content,
            keywords=payload.keywords,
            competitor_keywords=payload.competitor_keywords,
            advanced_config=payload.advanced_config,
            active_task_id=payload.active_task_id,
            client_message_id=payload.client_message_id,
            current_user=current_user,
            attachments=payload.attachments,
            knowledge_refs=payload.knowledge_refs,
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
