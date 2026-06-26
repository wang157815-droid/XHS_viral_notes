"""工作记忆工具：query_dataset / list_working_memory（cheap，不触发任何采集）。

采集类工具把完整结果写入 RunBlackboard 后只回"摘要 + 句柄"。模型用这两个工具
对已在工作记忆里的数据做筛选/排序/选列与盘点，无需重复采集，避免上下文爆炸。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from ..blackboard import QUERY_MAX_ROWS, get_blackboard
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


async def _query_dataset(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    handle = str(args.get("handle") or "").strip()
    if not handle:
        return ToolResult(ok=False, content="query_dataset 需要 handle 参数（来自采集类工具返回的句柄）", error="MISSING_ARGS")

    bb = get_blackboard(ctx)
    filters = args.get("filters") if isinstance(args.get("filters"), dict) else None
    sort_by = str(args.get("sort_by") or "").strip() or None
    descending = bool(args.get("descending", True))
    fields = _as_list(args.get("fields")) or None
    top_k_raw = args.get("top_k")
    top_k = int(top_k_raw) if isinstance(top_k_raw, (int, float, str)) and str(top_k_raw).strip().isdigit() else None

    try:
        rows, matched = bb.query(
            handle, filters=filters, sort_by=sort_by, descending=descending, top_k=top_k, fields=fields
        )
    except KeyError:
        available = [d.handle for d in bb.list_datasets()]
        return ToolResult(
            ok=False,
            content=f"工作记忆里没有句柄 {handle}。当前可用句柄：{available or '（空）'}",
            error="HANDLE_NOT_FOUND",
        )

    shown = len(rows)
    summary = (
        f"query {handle}：命中 {matched} 条，返回 {shown} 条"
        + (f"（已截断至 {QUERY_MAX_ROWS}）" if matched > shown else "")
        + (f"，按 {sort_by} {'降' if descending else '升'}序" if sort_by else "")
    )
    content = summary + "\n" + json.dumps(rows, ensure_ascii=False)
    logger.info("[tool.query_dataset] {}", summary)
    return ToolResult(ok=True, content=content, data={"handle": handle, "matched": matched, "rows": rows}, display=summary)


async def _list_working_memory(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    bb = get_blackboard(ctx)
    datasets = [
        {"handle": d.handle, "kind": d.kind, "count": d.count, "fields": d.fields(), "meta": d.meta}
        for d in bb.list_datasets()
    ]
    payload = {"datasets": datasets, "plan": bb.plan}
    if not datasets and not bb.plan:
        summary = "工作记忆为空（尚未采集任何数据，也未制定计划）"
    else:
        summary = f"工作记忆：{len(datasets)} 份数据集" + (f"，计划 {len(bb.plan)} 步" if bb.plan else "")
    return ToolResult(ok=True, content=summary + "\n" + json.dumps(payload, ensure_ascii=False), data=payload, display=summary)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="query_dataset",
            description=(
                "对工作记忆中已采集的数据集（由 collect_notes/search_notes/fetch_comments 等返回的 handle）"
                "做筛选/排序/选列，不触发任何新采集。用于从已取到的笔记/评论里挑出符合条件的子集再分析。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "description": "数据集句柄，如 ds:notes:1"},
                    "filters": {
                        "type": "object",
                        "description": "字段筛选。值可为标量(等值)或{op:operand}，op∈gte/gt/lte/lt/eq/ne/contains/in。"
                        "例：{\"likes\":{\"gte\":1000},\"note_type\":\"video\"}",
                    },
                    "sort_by": {"type": "string", "description": "排序字段，如 interaction_score / likes"},
                    "descending": {"type": "boolean", "description": "是否降序，默认 true"},
                    "top_k": {"type": "integer", "minimum": 1, "description": f"最多返回多少条，默认/上限 {QUERY_MAX_ROWS}"},
                    "fields": {"type": "array", "items": {"type": "string"}, "description": "只返回这些字段（投影），省略则返回全部字段"},
                },
                "required": ["handle"],
            },
            handler=_query_dataset,
            cost="cheap",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="list_working_memory",
            description="盘点当前工作记忆：列出已有数据集（句柄/类型/条数/字段）与当前计划。决定下一步前可先盘点已有数据，避免重复采集。",
            parameters={"type": "object", "properties": {}},
            handler=_list_working_memory,
            cost="cheap",
            category="capability",
        )
    )
