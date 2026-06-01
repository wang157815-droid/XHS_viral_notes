"""
评论分析报告 Excel 导出器。

基于模板文件 templates/comment_report_template.xlsx，用 openpyxl 纵向追加数据行。
格式（列宽、颜色、字体）由模板文件控制，本模块只负责数据绑定。

数据来源：TaskContext["comment_output"]，结构见 comment_pipeline.py。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, Side

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "comment_report_template.xlsx"


def _to_xhs_url(url: str) -> str:
    """将笔记链接中的 rednote.com 替换为 xiaohongshu.com，供业务人员直接访问。"""
    if not url:
        return url
    return url.replace("www.rednote.com", "www.xiaohongshu.com") \
              .replace("webapi.rednote.com", "edith.xiaohongshu.com")

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP_ALIGN = Alignment(horizontal="left", vertical="top", wrap_text=True)
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)
_DEFAULT_ROW_HEIGHT = 45.0   # 含换行内容行高
_METRIC_ROW_HEIGHT = 18.0    # 纯数字/短文本行高
_AI_COMMENT_LINE_HEIGHT = 18.0  # AI 生成评论每行高度
_AI_COMMENT_COL_IDX = 7         # Sheet1 第7列为 AI 生成评论
_AI_COMMENT_COL_PADDING = 4     # 列宽额外留白（字符数）


def _cell_write(ws, row: int, col: int, value: Any, *, center: bool = False) -> None:
    """写入单元格并设置边框和对齐。"""
    cell = ws.cell(row=row, column=col, value=value)
    cell.border = _BORDER
    cell.alignment = _CENTER_ALIGN if center else _WRAP_ALIGN


def _cn_display_width(text: str) -> float:
    """估算字符串的 Excel 显示宽度：中文/全角字符按 2 个单位，ASCII 按 1 个单位。"""
    return sum(2.0 if ord(ch) > 127 else 1.0 for ch in text)


def _write_sheet1(ws, rows: List[Dict[str, Any]]) -> None:
    """Sheet1 — 评论分析报告（聚合层）。
    列顺序：序号 / 笔记类别 / 评论类型 / 频次互动占比 / 评论内容核心 / 高热评论示例 / AI生成评论
    AI生成评论列（第7列）：5条带序号每条一行，列宽自动撑到最长一行，行高适配行数。
    """
    max_ai_line_width = 20.0  # 最小列宽保底

    for data_row_idx, row in enumerate(rows, start=2):
        r = data_row_idx
        _cell_write(ws, r, 1, row.get("seq") or data_row_idx, center=True)
        _cell_write(ws, r, 2, row.get("note_category") or "", center=True)
        _cell_write(ws, r, 3, row.get("comment_type") or "", center=True)
        _cell_write(ws, r, 4, row.get("ratio") or "0%", center=True)
        _cell_write(ws, r, 5, row.get("summary") or "")
        _cell_write(ws, r, 6, row.get("top_comment") or "")

        ai_text = row.get("ai_comment") or ""
        _cell_write(ws, r, 7, ai_text)

        # 逐行计算宽度，取最宽的一行更新最大值
        lines = ai_text.split("\n") if ai_text else []
        for line in lines:
            w = _cn_display_width(line)
            if w > max_ai_line_width:
                max_ai_line_width = w

        # 行高：每行约 18pt，再加 6pt 上下内边距
        line_count = max(len(lines), 1)
        ws.row_dimensions[r].height = max(_DEFAULT_ROW_HEIGHT, line_count * 18.0 + 6.0)

    # 最终统一设置第7列列宽（最长行 + 4字符留白）
    col_letter = ws.cell(row=1, column=7).column_letter
    ws.column_dimensions[col_letter].width = max_ai_line_width + 4


def _write_sheet2(ws, comments: List[Dict[str, Any]]) -> None:
    """Sheet2 — 评论数据源（原始评论明细）。
    列顺序：笔记类别 / 评论类型 / 原始评论 / 互动量(点赞数) / 来源笔记链接
    """
    for data_row_idx, c in enumerate(comments, start=2):
        r = data_row_idx
        _cell_write(ws, r, 1, c.get("note_category") or "", center=True)
        _cell_write(ws, r, 2, c.get("comment_type") or "", center=True)
        _cell_write(ws, r, 3, c.get("content") or "")
        _cell_write(ws, r, 4, c.get("like_count") or 0, center=True)
        _cell_write(ws, r, 5, _to_xhs_url(c.get("note_url") or c.get("note_id") or ""))
        ws.row_dimensions[r].height = _METRIC_ROW_HEIGHT


def _write_sheet3(ws, notes: List[Dict[str, Any]]) -> None:
    """Sheet3 — 笔记数据源（原始笔记明细）。
    列顺序：原始笔记链接/ID / 发布时间 / 标题关键词 / 笔记互动量 / 评论数量 / 笔记类别 / 评论区热点话题
    """
    for data_row_idx, n in enumerate(notes, start=2):
        r = data_row_idx
        _cell_write(ws, r, 1, _to_xhs_url(n.get("url") or ""))
        _cell_write(ws, r, 2, n.get("publish_time") or "", center=True)
        _cell_write(ws, r, 3, n.get("title") or "")
        _cell_write(ws, r, 4, n.get("interaction_score") or 0, center=True)
        _cell_write(ws, r, 5, n.get("comment_count") or 0, center=True)
        _cell_write(ws, r, 6, n.get("note_category") or "", center=True)
        _cell_write(ws, r, 7, n.get("topic_summary") or "")
        ws.row_dimensions[r].height = _DEFAULT_ROW_HEIGHT


def build_comment_excel(comment_output: Dict[str, Any]) -> bytes:
    """从 TaskContext["comment_output"] 构造 Excel 字节流。

    Args:
        comment_output: comment_pipeline.run_comment_pipeline 写入的数据字典

    Returns:
        xlsx 文件字节流
    """
    if not _TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"评论报告模板文件不存在: {_TEMPLATE_PATH}")

    wb = load_workbook(_TEMPLATE_PATH)

    sheet1_data: List[Dict[str, Any]] = comment_output.get("sheet1_analysis") or []
    sheet2_data: List[Dict[str, Any]] = comment_output.get("sheet2_comments") or []
    sheet3_data: List[Dict[str, Any]] = comment_output.get("sheet3_notes") or []

    sheet_names = wb.sheetnames
    logger.debug(f"[comment_excel] 模板 Sheet 名称: {sheet_names}")

    # 按位置匹配（index 0/1/2），容错用户命名的 Sheet 名
    if len(sheet_names) >= 1:
        _write_sheet1(wb.worksheets[0], sheet1_data)
    if len(sheet_names) >= 2:
        _write_sheet2(wb.worksheets[1], sheet2_data)
    if len(sheet_names) >= 3:
        _write_sheet3(wb.worksheets[2], sheet3_data)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    data = buf.read()
    logger.info(
        f"[comment_excel] 生成完成: sheet1={len(sheet1_data)}行 "
        f"sheet2={len(sheet2_data)}行 sheet3={len(sheet3_data)}行 size={len(data)}B"
    )
    return data
