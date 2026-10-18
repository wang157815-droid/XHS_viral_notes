"""阶段工具：把爆文分析 6 阶段流水线的每个阶段拆成可被 Agent 自由编排的独立工具。

与宏工具 run_viral_analysis（整包后台执行）并存：
- create_analysis_task：创建一个"分阶段执行"的分析任务（不自动启动流水线）；
- 各阶段工具在该任务上执行单个阶段 Agent，同步返回阶段结果摘要；
- get_task_progress：查看任务各阶段完成情况。

护栏（模型输出不可信，统一在工具内校验）：
- 归属：只能操作当前用户自己创建、且来源为阶段工具的任务；
- 前置：阶段依赖未满足时直接拒绝并提示缺失的阶段（数据完整性）；
- 并发：同一任务同一时间只运行一个阶段（asyncio.Lock）；
- 重复 / 重试：已完成阶段默认不重复执行（force=true 才重跑），单阶段最多失败 2 次。

阶段完成状态持久化在 datas/agent_stage_runs/<task_id>.json，服务重启后可继续编排。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

_SOURCE = "agent_stage_tools"
_MAX_FAILURES = 2
_STORE_DIR = Path(__file__).resolve().parents[5] / "datas" / "agent_stage_runs"
_locks: Dict[str, asyncio.Lock] = {}

# stage → (阶段 Agent 属性名列表, 必需前置阶段, 中文名)
_STAGES: Dict[str, Dict[str, Any]] = {
    "input_parser": {"agents": ["_input_parser"], "requires": [], "label": "意图解析"},
    "crawler": {"agents": ["_xhs_auth", "_crawler"], "requires": ["input_parser"], "label": "小红书采集"},
    "image_analysis": {"agents": ["_image"], "requires": ["crawler"], "label": "图文分析"},
    "video_analysis": {"agents": ["_video"], "requires": ["crawler"], "label": "视频分析"},
    "viral_model": {"agents": ["_viral_model", "_sheet2_narrative"], "requires": ["image_analysis"], "label": "爆文模型"},
    "insight": {"agents": ["_insight"], "requires": ["viral_model"], "label": "洞察提炼"},
    "rag": {"agents": ["_rag"], "requires": ["viral_model"], "label": "知识增强"},
    "canvas": {"agents": ["_canvas"], "requires": ["insight"], "label": "画布渲染"},
}


# ── 阶段状态存储 ──────────────────────────────────────────────────────────────
def _load_state(task_id: str) -> Dict[str, Any]:
    path = _STORE_DIR / f"{task_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("[stage_tools] 阶段状态文件损坏，重置 task={}", task_id)
    return {"completed": [], "failures": {}, "history": []}


def _save_state(task_id: str, state: Dict[str, Any]) -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    (_STORE_DIR / f"{task_id}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _require_owned_task(task_id: str, ctx: ToolContext):
    """返回 (record, error_result)。"""
    from ....infrastructure.repository import task_repository

    record = task_repository.get(task_id) if task_id else None
    if record is None:
        return None, ToolResult(ok=False, content=f"任务 {task_id!r} 不存在", error="TASK_NOT_FOUND")
    if record.owner_user_id != ctx.owner_user_id:
        return None, ToolResult(ok=False, content="无权操作该任务", error="FORBIDDEN")
    source = ((record.input_spec or {}).get("advanced_config") or {}).get("source")
    if source != _SOURCE:
        return None, ToolResult(
            ok=False,
            content="该任务不是通过 create_analysis_task 创建的分阶段任务，不能用阶段工具操作。",
            error="TASK_NOT_STAGED",
        )
    return record, None


# ── create_analysis_task ─────────────────────────────────────────────────────
def _clean_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    out: List[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[:5]


async def _create_analysis_task(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ...task_service import task_service

    keywords = _clean_list(args.get("keywords"))
    if not keywords:
        return ToolResult(ok=False, content="create_analysis_task 需要 keywords", error="MISSING_ARGS")
    advanced_config: Dict[str, Any] = {
        **(ctx.advanced_config or {}),
        "source": _SOURCE,
        "conversation_id": ctx.conversation_id,
    }
    for k in ("time_range", "note_type", "min_interaction", "sample_count"):
        if args.get(k):
            advanced_config[k] = args.get(k)
    result = task_service.create_task(
        owner_user_id=ctx.owner_user_id,
        raw_input=str(args.get("raw_input") or "；".join(keywords)),
        keywords=keywords,
        competitor_keywords=_clean_list(args.get("competitor_keywords")),
        advanced_config=advanced_config,
        idempotency_key=f"agent:{ctx.conversation_id}:staged:{uuid4().hex}",
        task_type="viral_analysis",
    )
    task_id = result.record.task_id
    _save_state(task_id, _load_state(task_id))
    ctx.extra.setdefault("created_tasks", []).append(
        {"task_id": task_id, "type": "viral_analysis", "keywords": keywords}
    )
    display = f"已创建分阶段分析任务 {task_id}（关键词：{', '.join(keywords)}）"
    return ToolResult(
        ok=True,
        content=(
            f"{display}。接下来按需调用阶段工具，第一步通常是 parse_task_input(task_id=\"{task_id}\")。"
        ),
        data={"task_id": task_id},
        display=display,
    )


# ── 阶段执行 ─────────────────────────────────────────────────────────────────
async def _ensure_running(task_id: str) -> None:
    from ....domain.events import TaskEventType
    from ....domain.task_status import TaskStatus
    from ....infrastructure.event_bus import task_event_bus
    from ....infrastructure.repository import task_repository
    from ...task_service import task_service

    status = task_repository.require(task_id).status
    if status == TaskStatus.PENDING:
        task_service.transition(task_id, TaskStatus.QUEUED)
        status = TaskStatus.QUEUED
    if status == TaskStatus.QUEUED:
        task_service.transition(task_id, TaskStatus.RUNNING)
        await task_event_bus.publish_event(
            task_id=task_id,
            type=TaskEventType.TASK_STATUS,
            payload={"status": TaskStatus.RUNNING.value, "progress": 3},
        )


async def _mark_completed(task_id: str) -> None:
    from ....domain.events import TaskEventType
    from ....domain.task_status import TaskStatus
    from ....infrastructure.event_bus import task_event_bus
    from ...task_service import task_service

    task_service.transition(task_id, TaskStatus.COMPLETED, progress=100)
    await task_event_bus.publish_event(
        task_id=task_id,
        type=TaskEventType.TASK_STATUS,
        payload={"status": TaskStatus.COMPLETED.value, "progress": 100},
    )
    await task_event_bus.publish_event(
        task_id=task_id, type=TaskEventType.DONE, payload={"task_id": task_id}
    )


def _summarize(stage: str, context: Any) -> str:
    """阶段完成后给模型的简短观察（只取关键计数，避免灌入大块数据）。"""
    try:
        if stage == "input_parser":
            parsed = (context.get("input_spec") or {}).get("parsed") or {}
            return "解析结果：" + json.dumps(parsed, ensure_ascii=False)[:1200]
        partition = {
            "crawler": "crawler_output",
            "image_analysis": "multimodal_output",
            "video_analysis": "multimodal_output",
            "viral_model": "viral_model_output",
            "insight": "semantic_output",
            "rag": "rag_output",
            "canvas": "canvas_document",
        }[stage]
        data = context.get(partition) or {}
        if isinstance(data, dict):
            parts = []
            for key, value in list(data.items())[:12]:
                if isinstance(value, (list, dict)):
                    parts.append(f"{key}={len(value)}项")
                elif isinstance(value, (str, int, float, bool)) and len(str(value)) <= 60:
                    parts.append(f"{key}={value}")
            return f"{partition} 已写入：" + ("，".join(parts) or "（结构为空）")
        return f"{partition} 已写入"
    except Exception:  # noqa: BLE001
        return "阶段已完成"


async def _run_stage(stage: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ....domain.task_context import task_context_store
    from ....infrastructure.repository import task_repository
    from ...agents import AgentContext
    from ...orchestrator import agent_orchestrator

    meta = _STAGES[stage]
    task_id = str(args.get("task_id") or "").strip()
    record, err = _require_owned_task(task_id, ctx)
    if err:
        return err
    if record.status.value in {"completed", "failed", "cancelled"} and stage != "canvas":
        return ToolResult(ok=False, content=f"任务已处于终态 {record.status.value}，不能继续执行阶段。", error="TASK_TERMINAL")

    lock = _locks.setdefault(task_id, asyncio.Lock())
    if lock.locked():
        return ToolResult(ok=False, content="该任务正在执行另一个阶段，请等待其完成后再调用。", error="STAGE_BUSY")

    async with lock:
        state = _load_state(task_id)
        done = set(state.get("completed") or [])
        missing = [r for r in meta["requires"] if r not in done]
        if missing:
            names = "、".join(f"{m}（{_STAGES[m]['label']}）" for m in missing)
            return ToolResult(ok=False, content=f"前置阶段未完成：{names}。请先执行它们。", error="PREREQUISITE_MISSING")
        if stage in done and not args.get("force"):
            return ToolResult(
                ok=True,
                content=f"阶段 {stage}（{meta['label']}）已完成，未重复执行（如需重跑传 force=true）。",
                display=f"{meta['label']}已完成（复用）",
            )
        failures = int((state.get("failures") or {}).get(stage, 0))
        if failures >= _MAX_FAILURES:
            return ToolResult(
                ok=False,
                content=f"阶段 {stage} 已连续失败 {failures} 次，不再重试。请向用户说明失败原因。",
                error="STAGE_RETRY_EXHAUSTED",
            )

        await _ensure_running(task_id)
        context = task_context_store.require(task_id)
        agent_ctx = AgentContext(task_id=task_id, task_context=context)
        started = time.monotonic()
        try:
            for attr in meta["agents"]:
                agent = getattr(agent_orchestrator, attr)
                await agent_orchestrator._run_stage(agent_ctx, agent, stage=stage)
        except Exception as exc:  # noqa: BLE001
            code = str(getattr(exc, "code", "") or type(exc).__name__)
            state.setdefault("failures", {})[stage] = failures + 1
            state.setdefault("history", []).append(
                {"stage": stage, "ok": False, "error": code, "at": time.time()}
            )
            _save_state(task_id, state)
            logger.warning("[stage_tools] task={} stage={} 失败: {} {}", task_id, stage, code, exc)
            hint = "（小红书数据源未授权，请提示用户前往设置页完成授权）" if "AUTH" in code else ""
            return ToolResult(
                ok=False,
                content=f"阶段 {stage}（{meta['label']}）执行失败：{code} {str(exc)[:300]}{hint}",
                error=code,
            )

        if stage not in done:
            state.setdefault("completed", []).append(stage)
        state.setdefault("failures", {}).pop(stage, None)
        state.setdefault("history", []).append(
            {"stage": stage, "ok": True, "ms": int((time.monotonic() - started) * 1000), "at": time.time()}
        )
        _save_state(task_id, state)

        extra = ""
        if stage == "canvas":
            if task_repository.require(task_id).status.value == "running":
                await _mark_completed(task_id)
            extra = f" Canvas 已生成，可在 /workspace?task={task_id} 查看。"
        summary = _summarize(stage, context)
        return ToolResult(
            ok=True,
            content=f"阶段 {stage}（{meta['label']}）完成。{summary}{extra}",
            data={"task_id": task_id, "stage": stage, "completed": state["completed"]},
            display=f"{meta['label']}完成",
        )


def _stage_handler(stage: str):
    async def _handler(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await _run_stage(stage, args, ctx)

    return _handler


async def _get_task_progress(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    task_id = str(args.get("task_id") or "").strip()
    record, err = _require_owned_task(task_id, ctx)
    if err:
        return err
    state = _load_state(task_id)
    done = state.get("completed") or []
    lines = []
    for stage, meta in _STAGES.items():
        if stage in done:
            mark = "已完成"
        elif all(r in done for r in meta["requires"]):
            mark = "可执行"
        else:
            mark = "等待前置：" + "、".join(r for r in meta["requires"] if r not in done)
        fails = (state.get("failures") or {}).get(stage)
        lines.append(f"- {stage}（{meta['label']}）：{mark}" + (f"，失败 {fails} 次" if fails else ""))
    return ToolResult(
        ok=True,
        content=f"任务 {task_id} 状态 {record.status.value}\n" + "\n".join(lines),
        data={"task_id": task_id, "status": record.status.value, "completed": done},
    )


# ── 注册 ─────────────────────────────────────────────────────────────────────
def _task_params(extra_desc: str = "") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "create_analysis_task 返回的任务 id"},
            "force": {"type": "boolean", "description": "阶段已完成时是否强制重跑，默认 false" + extra_desc},
        },
        "required": ["task_id"],
    }


_STAGE_TOOLS = [
    # name, stage, description, cost, confirm
    ("parse_task_input", "input_parser", "【阶段工具·意图解析】解析任务的原始需求，产出结构化检索规格（关键词、筛选条件、是否需要视频/RAG 等 pipeline_config）。", "cheap", False),
    ("crawl_task_notes", "crawler", "【阶段工具·采集】校验小红书授权后，按任务规格四源采集笔记（行业池/竞品/互动 TOP/SERP）。昂贵，会调用小红书接口。前置：parse_task_input。", "expensive", True),
    ("analyze_task_images", "image_analysis", "【阶段工具·图文分析】对已采集笔记做封面/标题/压字等 6 要素多模态标注。前置：crawl_task_notes。", "expensive", False),
    ("analyze_task_videos", "video_analysis", "【阶段工具·视频分析】对已采集的视频笔记做封面、节奏、镜头等分析。可选；解析结果 pipeline_config.run_video_analysis=false 时可跳过。前置：crawl_task_notes。", "expensive", False),
    ("build_viral_model", "viral_model", "【阶段工具·爆文模型】基于标注结果混合聚类，生成爆文模型矩阵并补全各要素 playbook 叙事。前置：analyze_task_images。", "cheap", False),
    ("generate_task_insights", "insight", "【阶段工具·洞察】基于爆文模型提炼痛点、SEO 与创作策略。前置：build_viral_model。", "cheap", False),
    ("retrieve_task_knowledge", "rag", "【阶段工具·知识增强】检索业务知识库，为任务补充约束与参考。可选。前置：build_viral_model。", "cheap", False),
    ("render_task_canvas", "canvas", "【阶段工具·画布渲染】汇总各阶段产出生成最终 Canvas（9 模块）并把任务标记为完成。前置：generate_task_insights（建议先完成需要的可选阶段）。", "cheap", True),
]


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="create_analysis_task",
            description="【阶段工具·创建任务】创建一个分阶段执行的爆文分析任务（不会自动运行），返回 task_id。需要灵活控制分析步骤时使用；只要标准完整产出时用 run_viral_analysis。",
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5, "description": "搜索关键词"},
                    "competitor_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "raw_input": {"type": "string", "description": "用户原始需求描述"},
                    "time_range": {"type": "string"},
                    "note_type": {"type": "string"},
                    "min_interaction": {"type": "string"},
                    "sample_count": {"type": "string"},
                },
                "required": ["keywords"],
            },
            handler=_create_analysis_task,
            cost="cheap",
            requires_confirmation=True,
            category="stage",
        )
    )
    for name, stage, description, cost, confirm in _STAGE_TOOLS:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=_task_params(),
                handler=_stage_handler(stage),
                cost=cost,  # type: ignore[arg-type]
                requires_confirmation=confirm,
                category="stage",
            )
        )
    registry.register(
        ToolSpec(
            name="get_task_progress",
            description="【阶段工具·进度】查看分阶段任务各阶段的完成 / 可执行 / 失败情况，用于决定下一步调用哪个阶段工具。",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
            handler=_get_task_progress,
            cost="cheap",
            category="stage",
        )
    )
