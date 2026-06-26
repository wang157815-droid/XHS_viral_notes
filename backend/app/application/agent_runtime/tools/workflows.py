"""宏工具：把现有成熟流水线封装成可被自主 Agent 调用的整包能力。

- run_viral_analysis  → AgentOrchestrator 爆文分析流水线（orchestration engine）
- run_comment_analysis → comment_pipeline 评论分析流水线

两者均创建任务并后台启动，工具立即返回 task_id；created_tasks 记入 ctx.extra
供上层做 TaskHandoff（前端可跳转 Canvas / 下载）。
"""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from loguru import logger

from ....domain.events import TaskEventType
from ....infrastructure.event_bus import task_event_bus
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


def _clean_keywords(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[:5]


async def _run_viral_analysis(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ...task_service import task_service
    from ...orchestration import get_orchestration_engine

    keywords = _clean_keywords(args.get("keywords"))
    if not keywords:
        return ToolResult(ok=False, content="run_viral_analysis 需要 keywords", error="MISSING_ARGS")
    competitor = _clean_keywords(args.get("competitor_keywords"))

    advanced_config: Dict[str, Any] = {
        **(ctx.advanced_config or {}),
        "source": "agent_runtime",
        "conversation_id": ctx.conversation_id,
    }
    for k in ("time_range", "note_type", "min_interaction", "sample_count"):
        if args.get(k):
            advanced_config[k] = args.get(k)

    result = task_service.create_task(
        owner_user_id=ctx.owner_user_id,
        raw_input=str(args.get("raw_input") or "；".join(keywords)),
        keywords=keywords,
        competitor_keywords=competitor,
        advanced_config=advanced_config,
        idempotency_key=f"agent:{ctx.conversation_id}:viral:{uuid4().hex}",
        task_type="viral_analysis",
    )
    task_id = result.record.task_id
    if result.created:
        await task_event_bus.publish_event(
            task_id=task_id,
            type=TaskEventType.TASK_STATUS,
            payload={"status": result.record.status.value, "progress": result.record.progress},
        )
        try:
            await get_orchestration_engine().start(task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[tool.run_viral_analysis] orchestrator start failed: {}", exc)

    ctx.extra.setdefault("created_tasks", []).append(
        {"task_id": task_id, "type": "viral_analysis", "keywords": keywords}
    )
    display = f"已启动爆文分析任务 {task_id}（关键词：{', '.join(keywords)}）"
    return ToolResult(
        ok=True,
        content=(
            f"{display}。该任务在后台运行 6 阶段流水线，完成后会生成 Canvas（9 模块）。"
            f"可在工作区查看：/workspace?task={task_id}"
        ),
        data={"task_id": task_id, "canvas_url_hint": f"/workspace?task={task_id}"},
        display=display,
    )


async def _run_comment_analysis(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    from ...task_service import task_service
    from ...comment_pipeline import run_comment_pipeline
    from ....infrastructure.execution.coordinator import execution_coordinator

    keywords = _clean_keywords(args.get("keywords"))
    if not keywords:
        return ToolResult(ok=False, content="run_comment_analysis 需要 keywords", error="MISSING_ARGS")
    top_notes = int(args.get("top_notes") or 0)
    top_comments = int(args.get("top_comments_per_note") or 5)

    result = task_service.create_task(
        owner_user_id=ctx.owner_user_id,
        raw_input=str(args.get("raw_input") or "；".join(keywords)),
        keywords=keywords,
        advanced_config={
            "source": "agent_runtime",
            "conversation_id": ctx.conversation_id,
            "top_notes": top_notes,
            "top_comments_per_note": top_comments,
        },
        idempotency_key=f"agent:{ctx.conversation_id}:comment:{uuid4().hex}",
        task_type="comment_analysis",
    )
    task_id = result.record.task_id
    if result.created:
        await task_event_bus.publish_event(
            task_id=task_id,
            type=TaskEventType.TASK_STATUS,
            payload={"status": result.record.status.value, "progress": result.record.progress},
        )
        _kwargs = dict(
            task_id=task_id,
            keywords=keywords,
            raw_input=str(args.get("raw_input") or "；".join(keywords)),
            top_notes=top_notes,
            top_comments_per_note=top_comments,
        )

        async def _runner(_handle: Any) -> None:
            await run_comment_pipeline(**_kwargs)

        await execution_coordinator.run_task(task_id, _runner)

    ctx.extra.setdefault("created_tasks", []).append(
        {"task_id": task_id, "type": "comment_analysis", "keywords": keywords}
    )
    display = f"已启动评论分析任务 {task_id}（关键词：{', '.join(keywords)}）"
    return ToolResult(
        ok=True,
        content=f"{display}。完成后会生成评论洞察报告（Excel）并推送下载链接。",
        data={"task_id": task_id},
        display=display,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_viral_analysis",
            description="【宏工具】运行完整的爆文分析流水线（采集→图文/视频分析→爆文模型矩阵→洞察/RAG→Canvas 渲染）。当用户需要标准的『爆文模型 / 内容矩阵』成熟产出时调用。后台异步执行，返回 task_id。",
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5, "description": "搜索关键词"},
                    "competitor_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "time_range": {"type": "string", "description": "可选时间范围，如『一周内』"},
                    "note_type": {"type": "string", "description": "可选笔记类型：视频/图文"},
                    "min_interaction": {"type": "string", "description": "可选互动量门槛，如『1000+』"},
                    "sample_count": {"type": "string", "description": "可选采集数量"},
                },
                "required": ["keywords"],
            },
            handler=_run_viral_analysis,
            cost="expensive",
            requires_confirmation=True,
            category="macro",
        )
    )
    registry.register(
        ToolSpec(
            name="run_comment_analysis",
            description="【宏工具】运行完整的评论分析流水线（采集笔记→提取高赞评论→分维度聚类→舆情洞察报告 Excel）。当用户需要标准的『评论洞察报告』时调用。后台异步执行，返回 task_id。",
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "top_notes": {"type": "integer", "minimum": 0, "description": "取互动量最高的前 N 条笔记，0=不限"},
                    "top_comments_per_note": {"type": "integer", "minimum": 1, "description": "每条笔记取前 K 条高赞评论，默认 5"},
                },
                "required": ["keywords"],
            },
            handler=_run_comment_analysis,
            cost="expensive",
            requires_confirmation=True,
            category="macro",
        )
    )
