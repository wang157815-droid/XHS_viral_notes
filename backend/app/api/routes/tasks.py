"""
任务相关 API：

- POST /tasks：创建任务（需 Idempotency-Key + Cookie 健康校验）
- GET /tasks：我的任务列表（管理员可 include_all=true）
- GET /tasks/{id}：任务详情
- POST /tasks/{id}/pause|resume|cancel：状态转换
- GET /tasks/{id}/canvas：获取画布
- GET /tasks/{id}/stream：SSE 事件流（带心跳 + 断线补发）
- POST /tasks/{id}/modules/{mid}/regenerate：模块重生成（幂等 + 乐观锁 + 权限）
- POST /tasks/{id}/modules/{mid}/paragraphs/{pid}/feedback：段落反馈写入 feedback_map
- POST /tasks/{id}/modules/{mid}/delete|restore：模块删除/恢复（状态机）
- GET /tasks/{id}/export/{format}：导出 Excel / JSON
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...application.auth import resolve_task_record
from ...application.auth.task_access import list_visible_tasks, task_audit_log
from ...application.idempotency import IdempotencyContext, require_idempotency
from ...application.optimistic_locking import ModuleVersionGuard, if_match_header
from ...application.orchestration import get_orchestration_engine
from ...application.module_regeneration import run_module_regeneration
from ...application.paragraph_feedback import merge_feedback_into_content, normalize_feedback_entry
from ...application.task_service import task_service
from ...core.responses import ok
from ...core.security import get_current_user
from ...domain.canvas import CanvasSchema
from ...domain.error_codes import ErrorCode, build_error
from ...domain.events import TaskEventType
from ...domain.module_status import ModuleStatus, module_status_machine
from ...domain.module_graph import module_graph
from ...domain.task_status import TaskStatus
from ...infrastructure.event_bus import task_event_bus
from ...services.cookie_health_service import cookie_health_service


router = APIRouter(prefix="/tasks", tags=["tasks"])


HEARTBEAT_INTERVAL_MS = int(os.getenv("SSE_HEARTBEAT_INTERVAL_MS", "15000"))
# 画布 schema 含四源样本时很容易超过 256KB；过小会导致整包被截断，前端收到空 payload 从而画布空白。
MAX_EVENT_BYTES = int(os.getenv("SSE_MAX_EVENT_BYTES", "2097152"))


class CreateTaskRequest(BaseModel):
    raw_input: str = Field(..., min_length=1)
    keywords: List[str] = Field(default_factory=list)
    # 用户显式给出的竞品检索词(品牌/产品名等),与主关键词分开爬 Sheet4;可不填由模型从 raw_input 抽取
    competitor_keywords: List[str] = Field(default_factory=list)
    advanced_config: Dict[str, Any] = Field(default_factory=dict)


@router.post("")
async def create_task(
    payload: CreateTaskRequest,
    current_user: dict = Depends(get_current_user),
    idem: IdempotencyContext = Depends(require_idempotency),
):
    if idem.cached_response is not None:
        return idem.cached_response

    try:
        cookie_health = cookie_health_service.get_cookie_health(current_user=current_user, force_check=False)
    except Exception as exc:
        await idem.abort()
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.SYSTEM_INTERNAL, f"Cookie 健康检查异常: {exc}"
            )["error"],
        )

    if cookie_health.get("status") == "expired":
        await idem.abort()
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.AUTH_COOKIE_EXPIRED,
                f"Cookie 已过期：{cookie_health['message']}。请重新登录后再发起任务。",
                details={"cookie_health": cookie_health},
            )["error"],
        )

    result = task_service.create_task(
        owner_user_id=str(current_user["user_id"]),
        raw_input=payload.raw_input,
        keywords=payload.keywords,
        competitor_keywords=payload.competitor_keywords,
        advanced_config=payload.advanced_config,
        idempotency_key=idem.key,
    )

    # 首次创建：发布一条 task_status 事件作为初始点，利于 SSE 订阅后立刻看到状态
    if result.created:
        await task_event_bus.publish_event(
            task_id=result.record.task_id,
            type=TaskEventType.TASK_STATUS,
            payload={
                "status": result.record.status.value,
                "progress": result.record.progress,
            },
        )
        # 启动 Agent 编排（后台执行，不阻塞 API 响应）
        try:
            await get_orchestration_engine().start(result.record.task_id)
        except Exception as exc:
            # 启动失败降级为失败任务，避免“创建了但永远挂起”
            await task_event_bus.publish_event(
                task_id=result.record.task_id,
                type=TaskEventType.ERROR,
                payload={"code": ErrorCode.SYSTEM_INTERNAL.value, "message": f"Orchestrator 启动失败: {exc}"},
            )

    response_body = {
        "ok": True,
        "data": {
            "task_id": result.record.task_id,
            "status": result.record.status.value,
            "created_at": result.record.created_at,
            "canvas_version": result.record.canvas_version,
            "idempotent_hit": not result.created,
            "cookie_health": cookie_health,
        },
    }
    await idem.record(status_code=200, body=response_body)
    return response_body


@router.get("")
async def list_tasks(
    include_all: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    records = list_visible_tasks(current_user, include_all=include_all, limit=limit)
    items = [
        {
            "task_id": r.task_id,
            "owner_user_id": r.owner_user_id,
            "status": r.status.value,
            "raw_input": (r.input_spec or {}).get("raw_input", ""),
            "keywords": r.keywords,
            "competitor_keywords": (r.input_spec or {}).get("competitor_keywords", []),
            "progress": r.progress,
            "collected_count": r.collected_count,
            "duration_seconds": r.duration_seconds,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in records
    ]
    return ok({"items": items, "include_all_effective": include_all and current_user.get("role") == "admin"})


@router.get("/{task_id}")
async def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    record = resolve_task_record(task_id, current_user, action="read")
    return ok(
        {
            "task_id": record.task_id,
            "owner_user_id": record.owner_user_id,
            "status": record.status.value,
            "raw_input": (record.input_spec or {}).get("raw_input", ""),
            "keywords": record.keywords,
            "competitor_keywords": (record.input_spec or {}).get("competitor_keywords", []),
            "progress": record.progress,
            "canvas_version": record.canvas_version,
            "context_version": record.context_version,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "error_code": record.error_code,
            "last_error": record.last_error,
        }
    )


async def _transition_endpoint(
    task_id: str,
    current_user: dict,
    target: TaskStatus,
    action_name: str,
):
    resolve_task_record(task_id, current_user, action=action_name, write=True)
    try:
        record = task_service.transition(task_id, target)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_TASK_STATE_INVALID, str(exc), details={"task_id": task_id}
            )["error"],
        )
    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.TASK_STATUS,
        payload={"status": record.status.value, "progress": record.progress},
    )
    return ok({"task_id": record.task_id, "status": record.status.value})


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, current_user: dict = Depends(get_current_user)):
    return await _transition_endpoint(task_id, current_user, TaskStatus.PAUSED, "pause")


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, current_user: dict = Depends(get_current_user)):
    return await _transition_endpoint(task_id, current_user, TaskStatus.RUNNING, "resume")


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: dict = Depends(get_current_user)):
    resolve_task_record(task_id, current_user, action="cancel", write=True)
    await get_orchestration_engine().cancel(task_id)
    try:
        record = task_service.transition(task_id, TaskStatus.CANCELLED)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_TASK_STATE_INVALID, str(exc), details={"task_id": task_id}
            )["error"],
        )
    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.TASK_STATUS,
        payload={"status": record.status.value, "progress": record.progress},
    )
    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.DONE,
        payload={"reason": "cancelled"},
    )
    return ok({"task_id": record.task_id, "status": record.status.value})


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """使用原任务的 input_spec（raw_input + keywords + advanced_config）重新提交一次。

    使用场景：历史页面里 failed / cancelled 的任务点击"重试"，无需用户重新填写。
    幂等键服务器端自动生成（与前端的新建流程区分开）。
    """
    from uuid import uuid4

    source = resolve_task_record(task_id, current_user, action="retry", write=False)
    input_spec = source.input_spec or {}
    raw_input = str(input_spec.get("raw_input") or "")
    if not raw_input:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_VALIDATION_FAILED,
                "原任务缺少 raw_input，无法重试",
                details={"task_id": task_id},
            )["error"],
        )

    try:
        cookie_health = cookie_health_service.get_cookie_health(
            current_user=current_user, force_check=False
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.SYSTEM_INTERNAL, f"Cookie 健康检查异常: {exc}"
            )["error"],
        )

    if cookie_health.get("status") == "expired":
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.AUTH_COOKIE_EXPIRED,
                f"Cookie 已过期：{cookie_health['message']}。请重新登录后再重试。",
                details={"cookie_health": cookie_health},
            )["error"],
        )

    new_idem = f"retry-{task_id}-{uuid4().hex[:12]}"
    result = task_service.create_task(
        owner_user_id=str(current_user["user_id"]),
        raw_input=raw_input,
        keywords=list(input_spec.get("keywords") or []),
        competitor_keywords=list(input_spec.get("competitor_keywords") or []),
        advanced_config=dict(input_spec.get("advanced_config") or {}),
        idempotency_key=new_idem,
    )

    new_task_id = result.record.task_id
    if result.created:
        await task_event_bus.publish_event(
            task_id=new_task_id,
            type=TaskEventType.TASK_STATUS,
            payload={
                "status": result.record.status.value,
                "progress": result.record.progress,
            },
        )
        try:
            await get_orchestration_engine().start(new_task_id)
        except Exception as exc:
            await task_event_bus.publish_event(
                task_id=new_task_id,
                type=TaskEventType.ERROR,
                payload={"code": ErrorCode.SYSTEM_INTERNAL.value, "message": f"Orchestrator 启动失败: {exc}"},
            )

    return ok(
        {
            "source_task_id": task_id,
            "new_task_id": new_task_id,
            "status": result.record.status.value,
            "idempotent_hit": not result.created,
        }
    )


@router.get("/{task_id}/canvas")
async def get_canvas(task_id: str, current_user: dict = Depends(get_current_user)):
    resolve_task_record(task_id, current_user, action="read_canvas")
    canvas = task_service.get_canvas(task_id)
    return ok(canvas.to_dict())


# ----------------------------------------------------------------------
# 模块写链路
# ----------------------------------------------------------------------
class RegenerateRequest(BaseModel):
    instruction: str = ""
    cascade: bool = False
    paragraph_id: Optional[str] = None
    feedback_hint: str = ""


def _assert_module_transition(module, target: ModuleStatus, *, module_id: str) -> None:
    try:
        module_status_machine.assert_transition(module.status, target)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INPUT_MODULE_STATE_INVALID,
                str(exc),
                details={"module_id": module_id, "from": module.status.value, "to": target.value},
            )["error"],
        )


def _mark_downstream_stale(canvas: CanvasSchema, changed_module_id: str) -> List[str]:
    def lookup(mid: str):
        m = canvas.find_module(mid)
        return m.status if m else None

    stale_ids = module_graph.cascade_mark_stale(changed_module_id, lookup)
    for mid in stale_ids:
        mod = canvas.find_module(mid)
        if mod and mod.status == ModuleStatus.READY:
            mod.status = ModuleStatus.STALE
            mod.version += 1
            mod.dirty_reason = f"上游 {changed_module_id} 已更新"
    return stale_ids


@router.post("/{task_id}/modules/{module_id}/regenerate")
async def regenerate_module(
    task_id: str,
    module_id: str,
    payload: RegenerateRequest,
    current_user: dict = Depends(get_current_user),
    idem: IdempotencyContext = Depends(require_idempotency),
    version_guard: ModuleVersionGuard = Depends(if_match_header),
):
    if idem.cached_response is not None:
        return idem.cached_response

    resolve_task_record(task_id, current_user, action=f"module.regenerate[{module_id}]", write=True)
    canvas = task_service.get_canvas(task_id)
    module = canvas.find_module(module_id)
    if not module:
        await idem.abort()
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND,
                f"模块不存在: {module_id}",
                details={"task_id": task_id, "module_id": module_id},
            )["error"],
        )

    version_guard.ensure_matches(module.version, module_id=module_id)
    _assert_module_transition(module, ModuleStatus.GENERATING, module_id=module_id)

    module.status = ModuleStatus.GENERATING
    module.version += 1
    module.dirty_reason = None
    cascaded = _mark_downstream_stale(canvas, module_id) if payload.cascade else []
    task_service.set_canvas(task_id, canvas)

    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.CANVAS_MODULE_UPDATED,
        payload={
            "module_id": module_id,
            "status": module.status.value,
            "version": module.version,
            "cascaded_stale": cascaded,
            "instruction": payload.instruction,
        },
    )

    body = {
        "ok": True,
        "data": {
            "task_id": task_id,
            "module_id": module_id,
            "status": module.status.value,
            "version": module.version,
            "cascaded_stale": cascaded,
        },
    }
    await idem.record(status_code=200, body=body)
    asyncio.create_task(
        run_module_regeneration(
            task_id=task_id,
            module_id=module_id,
            paragraph_id=payload.paragraph_id,
            instruction=payload.instruction,
            feedback_hint=payload.feedback_hint,
            cascade=payload.cascade,
        )
    )
    return body


class ParagraphFeedbackRequest(BaseModel):
    action: str = Field(..., min_length=1)
    edited_text: Optional[str] = None
    feedback_hint: Optional[str] = None


@router.post("/{task_id}/modules/{module_id}/paragraphs/{paragraph_id}/feedback")
async def submit_paragraph_feedback(
    task_id: str,
    module_id: str,
    paragraph_id: str,
    payload: ParagraphFeedbackRequest,
    current_user: dict = Depends(get_current_user),
    idem: IdempotencyContext = Depends(require_idempotency),
    version_guard: ModuleVersionGuard = Depends(if_match_header),
):
    if idem.cached_response is not None:
        return idem.cached_response

    resolve_task_record(
        task_id,
        current_user,
        action=f"module.paragraph_feedback[{module_id}:{paragraph_id}]",
        write=True,
    )
    canvas = task_service.get_canvas(task_id)
    module = canvas.find_module(module_id)
    if not module:
        await idem.abort()
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND,
                f"模块不存在: {module_id}",
                details={"task_id": task_id, "module_id": module_id},
            )["error"],
        )

    version_guard.ensure_matches(module.version, module_id=module_id)

    try:
        entry = normalize_feedback_entry(
            action=payload.action,
            edited_text=payload.edited_text,
            feedback_hint=payload.feedback_hint,
            actor=str(current_user.get("user_id") or ""),
        )
    except ValueError as exc:
        await idem.abort()
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.INPUT_VALIDATION_FAILED,
                str(exc),
                details={"paragraph_id": paragraph_id},
            )["error"],
        )

    new_content = merge_feedback_into_content(module.content or {}, paragraph_id, entry)
    snapshot = task_service.update_module_content(
        task_id,
        module_id,
        content=new_content,
        agent_id="paragraph_feedback",
    )
    if snapshot is None:
        await idem.abort()
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.SYSTEM_INTERNAL,
                "写入画布失败",
                details={"task_id": task_id, "module_id": module_id},
            )["error"],
        )

    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.CANVAS_MODULE_UPDATED,
        payload=snapshot,
    )

    body = {"ok": True, "data": {"task_id": task_id, "module_id": module_id, "paragraph_id": paragraph_id, "module": snapshot}}
    await idem.record(status_code=200, body=body)
    return body


class ModuleCommandRequest(BaseModel):
    reason: str = ""


@router.post("/{task_id}/modules/{module_id}/delete")
async def delete_module(
    task_id: str,
    module_id: str,
    payload: ModuleCommandRequest,
    current_user: dict = Depends(get_current_user),
    idem: IdempotencyContext = Depends(require_idempotency),
    version_guard: ModuleVersionGuard = Depends(if_match_header),
):
    if idem.cached_response is not None:
        return idem.cached_response

    resolve_task_record(task_id, current_user, action=f"module.delete[{module_id}]", write=True)
    canvas = task_service.get_canvas(task_id)
    module = canvas.find_module(module_id)
    if not module:
        await idem.abort()
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND, f"模块不存在: {module_id}",
                details={"task_id": task_id, "module_id": module_id}
            )["error"],
        )
    version_guard.ensure_matches(module.version, module_id=module_id)
    _assert_module_transition(module, ModuleStatus.DELETED, module_id=module_id)
    module.status = ModuleStatus.DELETED
    module.version += 1
    module.dirty_reason = payload.reason or None
    cascaded = _mark_downstream_stale(canvas, module_id)
    task_service.set_canvas(task_id, canvas)

    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.CANVAS_MODULE_UPDATED,
        payload={"module_id": module_id, "status": module.status.value, "version": module.version, "cascaded_stale": cascaded},
    )
    body = {
        "ok": True,
        "data": {"task_id": task_id, "module_id": module_id, "status": module.status.value, "version": module.version, "cascaded_stale": cascaded},
    }
    await idem.record(status_code=200, body=body)
    return body


@router.post("/{task_id}/modules/{module_id}/restore")
async def restore_module(
    task_id: str,
    module_id: str,
    payload: ModuleCommandRequest,
    current_user: dict = Depends(get_current_user),
    idem: IdempotencyContext = Depends(require_idempotency),
    version_guard: ModuleVersionGuard = Depends(if_match_header),
):
    if idem.cached_response is not None:
        return idem.cached_response

    resolve_task_record(task_id, current_user, action=f"module.restore[{module_id}]", write=True)
    canvas = task_service.get_canvas(task_id)
    module = canvas.find_module(module_id)
    if not module:
        await idem.abort()
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_NOT_FOUND, f"模块不存在: {module_id}",
                details={"task_id": task_id, "module_id": module_id}
            )["error"],
        )
    version_guard.ensure_matches(module.version, module_id=module_id)
    _assert_module_transition(module, ModuleStatus.READY, module_id=module_id)
    module.status = ModuleStatus.READY
    module.version += 1
    module.dirty_reason = None
    task_service.set_canvas(task_id, canvas)

    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.CANVAS_MODULE_UPDATED,
        payload={"module_id": module_id, "status": module.status.value, "version": module.version},
    )
    body = {
        "ok": True,
        "data": {"task_id": task_id, "module_id": module_id, "status": module.status.value, "version": module.version},
    }
    await idem.record(status_code=200, body=body)
    return body


# ----------------------------------------------------------------------
# SSE 主通道：心跳 + 断线补发 + 多订阅
# ----------------------------------------------------------------------
def _format_sse_event(event_type: str, data: Dict[str, Any], *, event_id: Optional[str] = None) -> str:
    serialized = json.dumps(data, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) > MAX_EVENT_BYTES:
        serialized = json.dumps(
            {"truncated": True, "type": event_type, "reason": "payload_exceeds_limit"},
            ensure_ascii=False,
        )
    lines: List[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {serialized}")
    return "\n".join(lines) + "\n\n"


@router.get("/{task_id}/stream")
async def stream_task(
    request: Request,
    task_id: str,
    current_user: dict = Depends(get_current_user),
    last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: Optional[str] = Query(default=None, alias="last_event_id"),
    last_sequence_id: Optional[int] = Query(default=None),
):
    resolve_task_record(task_id, current_user, action="stream")
    last_event_id = last_event_id_header or last_event_id_query

    async def event_generator():
        # 首个 ping 立即触发，验证链路通
        ping = task_event_bus.backlog  # type: ignore  # no-op to satisfy type
        now = datetime.now(timezone.utc).isoformat()
        yield _format_sse_event(
            TaskEventType.PING.value,
            {"server_time": now, "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS},
            event_id="ping-0",
        )
        del ping

        async def heartbeat_loop():
            interval = HEARTBEAT_INTERVAL_MS / 1000.0
            try:
                while True:
                    await asyncio.sleep(interval)
                    yield_hb = {
                        "server_time": datetime.now(timezone.utc).isoformat(),
                        "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS,
                    }
                    yield _format_sse_event(TaskEventType.PING.value, yield_hb, event_id=f"ping-{int(asyncio.get_running_loop().time())}")
            except asyncio.CancelledError:
                return

        subscription = task_event_bus.subscribe(
            task_id,
            last_event_id=last_event_id,
            last_sequence_id=last_sequence_id,
        )

        hb_task: Optional[asyncio.Task] = None
        hb_queue: asyncio.Queue = asyncio.Queue()

        async def _pump_heartbeat():
            async for chunk in heartbeat_loop():
                await hb_queue.put(chunk)

        hb_task = asyncio.create_task(_pump_heartbeat(), name=f"sse-heartbeat:{task_id}")

        try:
            sub_iter = subscription.__aiter__()
            next_task: Optional[asyncio.Task] = asyncio.create_task(sub_iter.__anext__())
            hb_pop_task: Optional[asyncio.Task] = asyncio.create_task(hb_queue.get())

            while True:
                if await request.is_disconnected():
                    break

                done, _pending = await asyncio.wait(
                    [next_task, hb_pop_task], return_when=asyncio.FIRST_COMPLETED
                )

                if next_task in done:
                    try:
                        event = next_task.result()
                    except StopAsyncIteration:
                        break
                    except Exception as exc:
                        payload = {"code": ErrorCode.SYSTEM_INTERNAL.value, "message": str(exc)}
                        yield _format_sse_event(TaskEventType.ERROR.value, payload)
                        break
                    data = event.to_dict()
                    yield _format_sse_event(event.type.value, data, event_id=event.event_id)
                    if event.type == TaskEventType.DONE:
                        break
                    next_task = asyncio.create_task(sub_iter.__anext__())

                if hb_pop_task in done:
                    chunk = hb_pop_task.result()
                    yield chunk
                    hb_pop_task = asyncio.create_task(hb_queue.get())
        finally:
            if hb_task:
                hb_task.cancel()
                try:
                    await hb_task
                except (asyncio.CancelledError, Exception):
                    pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------
@router.get("/{task_id}/export/json")
async def export_json(task_id: str, current_user: dict = Depends(get_current_user)):
    record = resolve_task_record(task_id, current_user, action="export.json")
    if record.status not in (TaskStatus.COMPLETED, TaskStatus.RUNNING, TaskStatus.PAUSED):
        # 允许运行中/暂停导出快照，未开始的拒绝
        if record.status == TaskStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=build_error(
                    ErrorCode.INPUT_TASK_STATE_INVALID,
                    "任务尚未产出可导出内容",
                    details={"task_id": task_id, "status": record.status.value},
                )["error"],
            )
    canvas = task_service.get_canvas(task_id)
    body = {
        "task": {
            "task_id": record.task_id,
            "owner_user_id": record.owner_user_id,
            "status": record.status.value,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        },
        "canvas": canvas.to_dict(),
    }
    filename = f"{record.task_id}.json"
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{task_id}/export/excel")
async def export_excel(task_id: str, current_user: dict = Depends(get_current_user)):
    """导出 7-sheet xlsx(对齐 docs/templates/kanglao_spec.md,4.3pre.5 落地)。

    数据源: canvas + TaskContext 的 crawler/multimodal/viral_model/semantic 4 分区
    总超时 60s,图片下载 15s 窗口内并发(Semaphore=10),超时降级为无图版本。
    """
    record = resolve_task_record(task_id, current_user, action="export.excel")
    canvas = task_service.get_canvas(task_id)

    try:
        from ...domain.task_context import task_context_store
        from ...services.canvas_export import build_excel_bytes
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.SYSTEM_DEPENDENCY,
                f"缺少依赖: {exc}",
            )["error"],
        )

    ctx = task_context_store.require(task_id)
    try:
        data = await build_excel_bytes(
            task_record=record,
            canvas=canvas,
            task_context=ctx,
            total_timeout=60.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.SYSTEM_INTERNAL,
                f"Excel 导出失败: {exc}",
            )["error"],
        ) from exc

    import io

    filename = f"{record.task_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# 审计访问以便追踪（防止未来新增 route 绕过）
__all__ = ["router", "task_audit_log"]
