"""报告导出工具 export_report：把 Agent 产出的分析写成可下载的 Markdown / Excel。

文件落地到 datas/agent_reports/{owner_user_id}/，返回带鉴权的下载 URL，
由 conversations 路由的 /reports/content 端点提供下载。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from loguru import logger

from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _reports_dir(owner_user_id: str) -> Path:
    safe_owner = "".join(c for c in (owner_user_id or "anon") if c.isalnum() or c in "-_") or "anon"
    d = _repo_root() / "datas" / "agent_reports" / safe_owner
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_stub(title: str) -> str:
    base = "".join(c for c in (title or "report") if c.isalnum() or c in "-_ ")[:40].strip().replace(" ", "_")
    return base or "report"


async def _export_report(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    title = str(args.get("title") or "分析报告").strip()
    body = str(args.get("markdown") or args.get("content") or "").strip()
    fmt = str(args.get("format") or "md").lower()
    if fmt not in {"md", "markdown", "excel", "xlsx"}:
        fmt = "md"
    if not body and not args.get("table"):
        return ToolResult(ok=False, content="export_report 需要 markdown 正文或 table 数据", error="MISSING_ARGS")

    owner = ctx.owner_user_id or "anon"
    out_dir = _reports_dir(owner)
    stub = _safe_stub(title)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid4().hex[:6]

    if fmt in {"excel", "xlsx"}:
        try:
            from openpyxl import Workbook
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, content=f"Excel 导出依赖缺失：{exc}", error="DEP_MISSING")
        filename = f"{stub}_{stamp}_{uid}.xlsx"
        path = out_dir / filename
        wb = Workbook()
        ws = wb.active
        ws.title = "报告"
        table = args.get("table")
        if isinstance(table, list) and table:
            for row in table:
                if isinstance(row, (list, tuple)):
                    ws.append([str(c) for c in row])
                elif isinstance(row, dict):
                    ws.append([str(v) for v in row.values()])
                else:
                    ws.append([str(row)])
        else:
            ws.append([title])
            for line in body.splitlines():
                ws.append([line])
        wb.save(path)
    else:
        filename = f"{stub}_{stamp}_{uid}.md"
        path = out_dir / filename
        path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")

    # owner 目录名与 _reports_dir 的清洗保持一致
    safe_owner = "".join(c for c in owner if c.isalnum() or c in "-_") or "anon"
    subpath = f"{safe_owner}/{filename}"
    download_url = f"/api/v1/conversations/reports/content?subpath={subpath}"
    display = f"已生成报告：{filename}"
    logger.info("[tool.export_report] {} -> {}", display, path)
    return ToolResult(
        ok=True,
        content=f"{display}。下载链接：{download_url}",
        data={"filename": filename, "download_url": download_url, "format": fmt, "subpath": subpath},
        display=display,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="export_report",
            description="把分析结果导出为可下载文件（Markdown 或 Excel）。需要给用户一份可下载报告时调用，调用后在最终回答里附上返回的下载链接。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "报告标题"},
                    "markdown": {"type": "string", "description": "报告正文（Markdown），format=md 时必填"},
                    "format": {"type": "string", "enum": ["md", "excel"], "description": "导出格式，默认 md"},
                    "table": {
                        "type": "array",
                        "description": "format=excel 时可传二维表（每行一个数组），优先于 markdown",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["title"],
            },
            handler=_export_report,
            cost="cheap",
            category="capability",
        )
    )
