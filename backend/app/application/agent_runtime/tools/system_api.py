"""系统接口工具：把部分后端 API 开放给 Agent 调用。

实现方式：直接调用 api/routes 里的路由函数，并传入当前会话用户（ctx.current_user），
因此权限 / 归属校验与 HTTP 接口完全一致（HTTPException → 工具失败结果）。

开放范围（用户确认）：
- 任务管理：列表 / 详情 / 画布（只读）；暂停、恢复、取消、重试（有副作用，需用户确认）
- 知识库与数据：知识库文档列表、运营指标汇总（仅管理员）、历史时间线
- 系统状态（只读）：Cookie 健康度、小红书授权状态、爬虫状态、关注关键词、系统模型设置

不开放：登录 / 用户管理 / 设置写入 / 删除类接口。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException

from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

_MAX_CONTENT = 6000


def _dump(data: Any, limit: int = _MAX_CONTENT) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"…（已截断，共 {len(text)} 字符）"


def _http_error(exc: HTTPException) -> ToolResult:
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message") or _dump(detail, 500)
        code = str(detail.get("code") or exc.status_code)
    else:
        message, code = str(detail), str(exc.status_code)
    return ToolResult(ok=False, content=f"接口调用失败（{exc.status_code}）：{message}", error=code)


async def _call(
    ctx: ToolContext,
    fn: Callable[[dict], Awaitable[Dict[str, Any]]],
    *,
    display: Optional[str] = None,
    shape: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> ToolResult:
    """执行路由函数并把 {ok, data} 包装为 ToolResult。"""
    if not ctx.current_user or not ctx.current_user.get("user_id"):
        return ToolResult(ok=False, content="缺少当前用户身份，无法调用系统接口", error="UNAUTHENTICATED")
    try:
        resp = await fn(ctx.current_user)
    except HTTPException as exc:
        return _http_error(exc)
    data = (resp or {}).get("data") or {}
    shaped = shape(data) if shape else data
    return ToolResult(ok=True, content=_dump(shaped), data=data, display=display)


def _task_id(args: Dict[str, Any]) -> str:
    return str(args.get("task_id") or "").strip()


# ── 任务管理 ─────────────────────────────────────────────────────────────────
async def _list_tasks(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.history import list_history_tasks

    page_size = max(1, min(int(args.get("limit") or 20), 50))
    return await _call(
        ctx,
        lambda u: list_history_tasks(
            keyword=args.get("keyword") or None,
            status=args.get("status") or None,
            time_range="7d",
            page=1,
            page_size=page_size,
            current_user=u,
        ),
        display="已查询历史任务",
    )


async def _get_task(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.tasks import get_task

    return await _call(ctx, lambda u: get_task(_task_id(args), current_user=u))


def _shape_canvas(module_id: str):
    def shape(data: Dict[str, Any]) -> Any:
        modules = data.get("modules") or []
        if module_id:
            for mod in modules:
                if mod.get("module_id") == module_id:
                    return mod
            return {"error": f"模块 {module_id} 不存在", "module_ids": [m.get("module_id") for m in modules]}
        return {
            "title": data.get("title"),
            "canvas_version": data.get("canvas_version"),
            "modules": [
                {k: m.get(k) for k in ("module_id", "title", "status", "summary")} for m in modules
            ],
            "hint": "传 module_id 可查看单个模块完整内容",
        }

    return shape


async def _get_task_canvas(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.tasks import get_canvas

    module_id = str(args.get("module_id") or "").strip()
    return await _call(
        ctx,
        lambda u: get_canvas(_task_id(args), current_user=u),
        shape=_shape_canvas(module_id),
        display="已读取任务画布",
    )


def _action_handler(route_name: str, label: str):
    async def handler(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ....api.routes import tasks as task_routes

        fn = getattr(task_routes, route_name)
        task_id = _task_id(args)
        return await _call(ctx, lambda u: fn(task_id, current_user=u), display=f"已{label}任务 {task_id}")

    return handler


# ── 知识库与数据 ─────────────────────────────────────────────────────────────
async def _list_knowledge_documents(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.knowledge import list_documents

    return await _call(ctx, lambda u: list_documents(current_user=u), display="已查询知识库文档")


async def _get_metrics_summary(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.metrics import get_metrics_summary
    from ....core.security import RoleLevel, role_allows

    if not role_allows(ctx.current_user.get("role"), RoleLevel.admin):
        return ToolResult(ok=False, content="运营指标仅管理员可查看", error="FORBIDDEN")
    hours = max(1, min(int(args.get("window_hours") or 24), 24 * 30))
    return await _call(ctx, lambda u: get_metrics_summary(window_hours=hours, current_user=u), display="已查询运营指标")


async def _get_history_timeline(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.history import list_history_timeline

    limit = max(1, min(int(args.get("limit") or 30), 100))
    return await _call(
        ctx,
        lambda u: list_history_timeline(
            keyword=args.get("keyword") or None,
            include_archived=False,
            limit=limit,
            current_user=u,
        ),
        display="已查询历史时间线",
    )


# ── 系统状态（只读） ─────────────────────────────────────────────────────────
async def _get_cookie_health(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.settings import get_cookie_health

    return await _call(ctx, lambda u: get_cookie_health(force=False, current_user=u), display="已检查 Cookie 健康度")


async def _get_xhs_auth_status(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.xhs_auth import check_my_credential_status

    return await _call(ctx, lambda u: check_my_credential_status(force=False, current_user=u), display="已检查小红书授权")


async def _get_crawler_status(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.settings import get_crawler_status

    return await _call(ctx, lambda u: get_crawler_status(current_user=u), display="已查询爬虫状态")


async def _get_focus_keywords(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.settings import get_focus_keywords

    return await _call(ctx, lambda u: get_focus_keywords(current_user=u), display="已读取关注关键词")


async def _get_system_settings(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....api.routes.settings import get_system_settings

    return await _call(ctx, lambda u: get_system_settings(current_user=u), display="已读取系统设置")


# ── 注册 ─────────────────────────────────────────────────────────────────────
_EMPTY = {"type": "object", "properties": {}}
_TASK_ID = {
    "type": "object",
    "properties": {"task_id": {"type": "string", "description": "任务 id（可先用 list_my_tasks 查询）"}},
    "required": ["task_id"],
}

_TOOLS = [
    # name, description, parameters, handler, confirm
    (
        "list_my_tasks",
        "【系统接口·任务】查询当前用户的历史分析任务列表（管理员可见全部），支持关键词与状态过滤。",
        {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "按任务 id / 关键词模糊过滤"},
                "status": {"type": "string", "enum": ["pending", "queued", "running", "paused", "completed", "failed", "cancelled"]},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最多 50"},
            },
        },
        _list_tasks,
        False,
    ),
    ("get_task_detail", "【系统接口·任务】查看任务详情：状态、进度、关键词、错误信息等。", _TASK_ID, _get_task, False),
    (
        "get_task_canvas",
        "【系统接口·任务】读取任务的分析画布（Canvas）结果。不传 module_id 返回模块概览；传 module_id 返回该模块完整内容。",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "module_id": {"type": "string", "description": "可选，模块 id，如 mod-viral-model-matrix"},
            },
            "required": ["task_id"],
        },
        _get_task_canvas,
        False,
    ),
    ("pause_task", "【系统接口·任务】暂停运行中的任务。", _TASK_ID, _action_handler("pause_task", "暂停"), True),
    ("resume_task", "【系统接口·任务】恢复已暂停的任务。", _TASK_ID, _action_handler("resume_task", "恢复"), True),
    ("cancel_task", "【系统接口·任务】取消任务（不可撤销）。", _TASK_ID, _action_handler("cancel_task", "取消"), True),
    (
        "retry_task",
        "【系统接口·任务】用原任务的输入重新提交一个新任务（常用于失败 / 已取消任务），返回 new_task_id。会重新采集小红书数据。",
        _TASK_ID,
        _action_handler("retry_task", "重试"),
        True,
    ),
    ("list_knowledge_documents", "【系统接口·知识库】列出知识库中的文档（标题、分块数、状态等）。", _EMPTY, _list_knowledge_documents, False),
    (
        "get_metrics_summary",
        "【系统接口·数据】运营指标汇总（任务量、成功率、模型调用与成本等），仅管理员可用。",
        {"type": "object", "properties": {"window_hours": {"type": "integer", "description": "统计窗口小时数，默认 24，最多 720"}}},
        _get_metrics_summary,
        False,
    ),
    (
        "get_history_timeline",
        "【系统接口·数据】按时间倒序列出最近的任务与对话记录。",
        {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 30，最多 100"},
            },
        },
        _get_history_timeline,
        False,
    ),
    ("get_cookie_health", "【系统接口·状态】检查当前小红书 Cookie 的健康度（是否过期）。采集前可先检查。", _EMPTY, _get_cookie_health, False),
    ("get_xhs_auth_status", "【系统接口·状态】查看当前用户小红书数据源授权（绑定账号）状态。", _EMPTY, _get_xhs_auth_status, False),
    ("get_crawler_status", "【系统接口·状态】查看后台爬虫预热状态：最近一次运行、下次运行时间、累计笔记数。", _EMPTY, _get_crawler_status, False),
    ("get_focus_keywords", "【系统接口·状态】读取系统配置的关注关键词列表。", _EMPTY, _get_focus_keywords, False),
    ("get_system_settings", "【系统接口·状态】读取系统设置（文本 / 视觉 / Embedding 模型、视频分析开关、采集后端等）。", _EMPTY, _get_system_settings, False),
]


def register(registry: ToolRegistry) -> None:
    for name, description, parameters, handler, confirm in _TOOLS:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
                cost="cheap",
                requires_confirmation=confirm,
                category="capability",
            )
        )
