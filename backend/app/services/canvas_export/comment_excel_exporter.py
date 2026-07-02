"""
评论分析报告 Excel 导出器。

v2（当前默认）：从 comment_output 直接构建 2-Sheet 工作簿（无模板依赖）
  Sheet1 "评论数据源"：评论内容 / 点赞数 / 评论者昵称 / 是否子评论 / 来源笔记标题 / 来源笔记链接
  Sheet2 "笔记数据源"：笔记链接 / 发布时间 / 标题 / 互动量 / 爬取评论总数

v1（保留，当 comment_output["version"] != "v2" 时调用）：
  使用 templates/comment_report_template.xlsx，3-Sheet 旧结构。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "comment_report_template.xlsx"

# ── 通用样式常量 ───────────────────────────────────────────────────────────────
_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP_ALIGN = Alignment(horizontal="left", vertical="top", wrap_text=True)
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)
_DEFAULT_ROW_HEIGHT = 45.0
_METRIC_ROW_HEIGHT = 18.0
_AI_COMMENT_LINE_HEIGHT = 18.0
_AI_COMMENT_COL_IDX = 7
_AI_COMMENT_COL_PADDING = 4


def _to_xhs_url(url: str) -> str:
    """将笔记链接中的 rednote.com 替换为 xiaohongshu.com，供业务人员直接访问。"""
    if not url:
        return url
    return url.replace("www.rednote.com", "www.xiaohongshu.com") \
              .replace("webapi.rednote.com", "edith.xiaohongshu.com")


# Excel 单元格不允许的控制字符:0x00-0x08、0x0B、0x0C、0x0E-0x1F。
# 保留 0x09(\t)、0x0A(\n)、0x0D(\r)——wrap_text 需要 \n 换行。
# 小红书评论/文案里偶尔混入这些控制符,会让 openpyxl 抛
# IllegalCharacterError("... cannot be used in worksheets."),评论报告导出 500。
_ILLEGAL_XLSX_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_for_xlsx(value: Any) -> Any:
    """剥离 Excel 单元格不允许的控制字符,仅对字符串生效。"""
    if isinstance(value, str):
        return _ILLEGAL_XLSX_CHARS_RE.sub("", value)
    return value


def _cell_write(ws, row: int, col: int, value: Any, *, center: bool = False) -> None:
    """写入单元格并设置边框和对齐。"""
    value = _sanitize_for_xlsx(value)
    cell = ws.cell(row=row, column=col, value=value)
    cell.border = _BORDER
    cell.alignment = _CENTER_ALIGN if center else _WRAP_ALIGN


def _cn_display_width(text: str) -> float:
    """估算字符串的 Excel 显示宽度：中文/全角字符按 2 个单位，ASCII 按 1 个单位。"""
    return sum(2.0 if ord(ch) > 127 else 1.0 for ch in text)


# ── v1 旧版 Sheet 写入函数（保留不删，_build_v1_excel 使用） ─────────────────────

def _write_sheet1(ws, rows: List[Dict[str, Any]]) -> None:
    """[v1] Sheet1 — 评论分析报告（聚合层）。
    列顺序：序号 / 笔记类别 / 评论类型 / 频次互动占比 / 评论内容核心 / 高热评论示例 / AI生成评论
    """
    max_ai_line_width = 20.0

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

        lines = ai_text.split("\n") if ai_text else []
        for line in lines:
            w = _cn_display_width(line)
            if w > max_ai_line_width:
                max_ai_line_width = w

        line_count = max(len(lines), 1)
        ws.row_dimensions[r].height = max(_DEFAULT_ROW_HEIGHT, line_count * 18.0 + 6.0)

    col_letter = ws.cell(row=1, column=7).column_letter
    ws.column_dimensions[col_letter].width = max_ai_line_width + 4


def _write_sheet2(ws, comments: List[Dict[str, Any]]) -> None:
    """[v1] Sheet2 — 评论数据源（原始评论明细）。
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
    """[v1] Sheet3 — 笔记数据源（原始笔记明细）。
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


# ── v2 新版 2-Sheet Excel 构建（不依赖模板） ──────────────────────────────────

_V2_HEADER_FILL = PatternFill(fill_type="solid", fgColor="2D5F8A")
_V2_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_V2_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)


def _write_v2_header(ws, headers: List[str], col_widths: Optional[List[float]] = None) -> None:
    """写 v2 表头行（深蓝背景，白色加粗字体）。"""
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _V2_HEADER_FILL
        cell.font = _V2_HEADER_FONT
        cell.alignment = _V2_HEADER_ALIGN
        cell.border = _BORDER
    ws.row_dimensions[1].height = 22.0
    if col_widths:
        for col_idx, width in enumerate(col_widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width


def _build_v2_excel(comment_output: Dict[str, Any]) -> bytes:
    """[v2] 构建 2-Sheet Excel（不依赖模板文件）。"""
    wb = Workbook()

    # ── Sheet1：评论数据源 ────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "评论数据源"

    # 列1 为「维度」，方便按维度筛选 / 排序
    s1_headers = ["维度", "评论内容", "点赞数", "评论者昵称", "是否子评论", "来源笔记标题", "来源笔记链接"]
    s1_widths = [14.0, 60.0, 8.0, 14.0, 10.0, 30.0, 50.0]
    _write_v2_header(ws1, s1_headers, s1_widths)

    comments: List[Dict] = comment_output.get("sheet1_comments") or []
    for row_idx, c in enumerate(comments, start=2):
        _cell_write(ws1, row_idx, 1, c.get("dimension") or "", center=True)
        _cell_write(ws1, row_idx, 2, c.get("content") or "")
        _cell_write(ws1, row_idx, 3, c.get("like_count") or 0, center=True)
        _cell_write(ws1, row_idx, 4, c.get("author") or "", center=True)
        _cell_write(ws1, row_idx, 5, "是" if c.get("is_sub_comment") else "否", center=True)
        _cell_write(ws1, row_idx, 6, c.get("note_title") or "")
        _cell_write(ws1, row_idx, 7, _to_xhs_url(c.get("note_url") or ""))
        ws1.row_dimensions[row_idx].height = _METRIC_ROW_HEIGHT

    ws1.freeze_panes = "A2"

    # ── Sheet2：笔记数据源 ────────────────────────────────────────────────────
    ws2 = wb.create_sheet("笔记数据源")

    # 列6/7 为「笔记具体内容」和「标签内容」
    s2_headers = ["笔记链接", "发布时间", "标题", "互动量", "爬取评论总数", "笔记具体内容", "标签内容"]
    s2_widths = [55.0, 12.0, 40.0, 10.0, 12.0, 60.0, 30.0]
    _write_v2_header(ws2, s2_headers, s2_widths)

    notes: List[Dict] = comment_output.get("sheet2_notes") or []
    for row_idx, n in enumerate(notes, start=2):
        _cell_write(ws2, row_idx, 1, _to_xhs_url(n.get("url") or ""))
        _cell_write(ws2, row_idx, 2, n.get("publish_time") or "", center=True)
        _cell_write(ws2, row_idx, 3, n.get("title") or "")
        _cell_write(ws2, row_idx, 4, n.get("interaction_score") or 0, center=True)
        _cell_write(ws2, row_idx, 5, n.get("fetched_comment_count") or 0, center=True)
        _cell_write(ws2, row_idx, 6, n.get("desc") or "")
        _cell_write(ws2, row_idx, 7, n.get("tags") or "")
        ws2.row_dimensions[row_idx].height = _DEFAULT_ROW_HEIGHT

    ws2.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    data = buf.read()
    logger.info(
        f"[comment_excel v2] 生成完成: "
        f"sheet1(评论)={len(comments)}行 sheet2(笔记)={len(notes)}行 size={len(data)}B"
    )
    return data


def _build_v1_excel(comment_output: Dict[str, Any]) -> bytes:
    """[v1] 使用模板文件构建旧版 3-Sheet Excel。"""
    if not _TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"评论报告模板文件不存在: {_TEMPLATE_PATH}")

    wb = load_workbook(_TEMPLATE_PATH)

    sheet1_data: List[Dict[str, Any]] = comment_output.get("sheet1_analysis") or []
    sheet2_data: List[Dict[str, Any]] = comment_output.get("sheet2_comments") or []
    sheet3_data: List[Dict[str, Any]] = comment_output.get("sheet3_notes") or []

    sheet_names = wb.sheetnames
    logger.debug(f"[comment_excel v1] 模板 Sheet 名称: {sheet_names}")

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
        f"[comment_excel v1] 生成完成: sheet1={len(sheet1_data)}行 "
        f"sheet2={len(sheet2_data)}行 sheet3={len(sheet3_data)}行 size={len(data)}B"
    )
    return data


# ── 对外统一入口 ───────────────────────────────────────────────────────────────

def build_comment_excel(comment_output: Dict[str, Any]) -> bytes:
    """从 TaskContext["comment_output"] 构造 Excel 字节流。

    自动根据 comment_output["version"] 选择 v2（2-Sheet）或 v1（3-Sheet 模板）。
    """
    version = str(comment_output.get("version") or "v1")
    if version == "v2":
        return _build_v2_excel(comment_output)
    return _build_v1_excel(comment_output)
