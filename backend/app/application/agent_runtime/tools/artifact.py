"""结构化产物工具：present_artifact（cheap）。

让自主 Agent 在最终回答之外，产出一个可在对话内联渲染的结构化交付物：
- table   ：动态表格（逐条拆解 N 篇笔记的首选，列可自定义）
- sections：分组要点（每组一个小标题 + 若干要点）

产物写入 ctx.extra["artifacts"]，runtime 发 artifact 事件，conversation_service 落库到
ChatMessage.artifacts，前端用 AgentArtifactCard 渲染。表格可由黑板句柄直接取行。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from ..blackboard import get_blackboard
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


_MAX_ROWS = 80
_MAX_COLS = 12


def _as_str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _build_table_rows_from_handle(
    ctx: ToolContext, handle: str, columns: List[str], *, sort_by: Optional[str], top_k: Optional[int]
) -> List[List[str]]:
    bb = get_blackboard(ctx)
    rows, _matched = bb.query(handle, sort_by=sort_by, descending=True, top_k=top_k or _MAX_ROWS, fields=columns)
    out: List[List[str]] = []
    for r in rows:
        out.append([_cell(r.get(c)) for c in columns])
    return out


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value)
    return str(value)


def _normalize_rows(rows: Any, ncols: int) -> List[List[str]]:
    out: List[List[str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:_MAX_ROWS]:
        if isinstance(row, (list, tuple)):
            out.append([_cell(c) for c in row][:ncols])
        elif isinstance(row, dict):
            out.append([_cell(v) for v in row.values()][:ncols])
        else:
            out.append([_cell(row)])
    return out


def _normalize_sections(sections: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(sections, list):
        return out
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or sec.get("title") or "").strip()
        points = _as_str_list(sec.get("points") or sec.get("items") or [])
        body = str(sec.get("body") or "").strip()
        if heading or points or body:
            out.append({"heading": heading, "points": points, "body": body})
    return out


async def _present_artifact(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    kind = str(args.get("kind") or args.get("type") or "table").strip().lower()
    title = str(args.get("title") or "分析结果").strip()
    summary = str(args.get("summary") or "").strip()

    artifact: Dict[str, Any] = {
        "artifact_id": f"art_{uuid4().hex[:10]}",
        "type": "sections" if kind in {"sections", "section", "list"} else "table",
        "title": title,
        "summary": summary,
    }

    if artifact["type"] == "table":
        columns = _as_str_list(args.get("columns"))[:_MAX_COLS]
        if not columns:
            return ToolResult(ok=False, content="present_artifact(table) 需要 columns（列名数组）", error="MISSING_ARGS")
        source_handle = str(args.get("source_handle") or "").strip()
        if source_handle:
            sort_by = str(args.get("sort_by") or "").strip() or None
            top_k = int(args.get("top_k")) if str(args.get("top_k") or "").isdigit() else None
            try:
                rows = _build_table_rows_from_handle(ctx, source_handle, columns, sort_by=sort_by, top_k=top_k)
            except KeyError:
                return ToolResult(ok=False, content=f"present_artifact: 工作记忆里没有句柄 {source_handle}", error="HANDLE_NOT_FOUND")
        else:
            rows = _normalize_rows(args.get("rows"), len(columns))
        if not rows:
            return ToolResult(ok=False, content="present_artifact(table) 没有可呈现的数据行（rows 为空且 source_handle 未命中）", error="EMPTY_ROWS")
        artifact["columns"] = columns
        artifact["rows"] = rows
        display = f"已生成表格产物「{title}」（{len(columns)} 列 × {len(rows)} 行）"
    else:
        sections = _normalize_sections(args.get("sections"))
        if not sections:
            return ToolResult(ok=False, content="present_artifact(sections) 需要 sections（[{heading, points|body}]）", error="MISSING_ARGS")
        artifact["sections"] = sections
        display = f"已生成分组要点产物「{title}」（{len(sections)} 组）"

    ctx.extra.setdefault("artifacts", []).append(artifact)
    logger.info("[tool.present_artifact] {}", display)
    return ToolResult(
        ok=True,
        content=f"{display}。该产物已在对话中以卡片形式展示给用户；最终回答可据此给出结论，无需再重复整张表格。",
        data={"artifact": artifact},
        display=display,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="present_artifact",
            description=(
                "生成一个在对话内联展示的结构化产物卡片。"
                "type=table 用于逐条对比/拆解（动态列+行，可直接用 source_handle 从工作记忆取行）；"
                "type=sections 用于分组要点（每组小标题+要点）。"
                "『逐条罗列并拆解 N 篇笔记』这类需求应优先用 table 产物，而不是把大表塞进 Markdown 正文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["table", "sections"], "description": "产物类型"},
                    "title": {"type": "string", "description": "产物标题"},
                    "summary": {"type": "string", "description": "一句话说明（可选）"},
                    "columns": {"type": "array", "items": {"type": "string"}, "description": "table 列名。配合 source_handle 时，列名应为数据集字段名"},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "table 行（每行一个数组）。与 source_handle 二选一"},
                    "source_handle": {"type": "string", "description": "从工作记忆某数据集句柄按 columns 自动取行（如 ds:notes:1）"},
                    "sort_by": {"type": "string", "description": "source_handle 取行时的排序字段"},
                    "top_k": {"type": "integer", "minimum": 1, "description": "source_handle 取行时最多多少行"},
                    "sections": {
                        "type": "array",
                        "description": "sections 类型的分组：[{heading, points:[...]}|{heading, body}]",
                        "items": {"type": "object"},
                    },
                },
                "required": ["title"],
            },
            handler=_present_artifact,
            cost="cheap",
            category="capability",
        )
    )
