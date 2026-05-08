"""
Sheet2「爆文总结详情2」playbook 矩阵布局引擎。

将 viral_model 的 elements(分类 + 占比 + 示例)渲染为:
  B 列表头行写要素名 + 后续行写「概括/解释/示例」行标签 + 横向多轨(不规则 col_span)
  × 表头行 + 概括/解释/示例三行内容; 示例行可嵌图。
  A 列收窄为 0.5（隐藏），要素名已移至 B 列表头位置。

要素级列宽模板对齐 kanglao_spec 中 C 标题 / D 切入点等典型合并。
列宽采用动态测量：按各轨实际内容比例分配，总宽贴近 page_width_chars，避免横向滚动条。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple

from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .openpyxl_helpers import safe_set_cell_value, safe_write_merged_cell

# 与 excel_exporter._ELEMENT_ORDER 顺序一致时由调用方传入 element_code
_COL_SPAN_TEMPLATES: Dict[str, Tuple[int, ...]] = {
    # C1 占双列等
    "C_title": (2, 1, 1, 1, 1),
    # 三主轨: 痛点区双列 + 痒点双列 + 单列(与模板常见形态一致); 轨数不足则截断,超出则补 1
    "D_opening": (2, 2, 1),
}


@dataclass(frozen=True)
class Sheet2PlaybookStyles:
    model_header_font: Font
    model_header_fill: PatternFill
    rubric_header_font: Font
    rubric_header_fill: PatternFill
    row_label_font: Font
    element_merged_font: Font
    element_merged_fill: PatternFill
    data_font: Font          # 概括/解释/示例内容行字体（等线 10 号）
    center_align: Alignment
    data_align: Alignment


def axis_letter_from_element_code(element_code: str) -> str:
    if not element_code:
        return "X"
    return element_code[0].upper()


def _default_track_id(element_code: str, index: int) -> str:
    return f"{axis_letter_from_element_code(element_code)}{index}"


def compute_track_col_spans(element_code: str, categories: Sequence[Dict[str, Any]]) -> List[int]:
    n = len(categories)
    if n == 0:
        return []
    tpl = list(_COL_SPAN_TEMPLATES.get(element_code, ()))
    spans: List[int] = []
    for i, cat in enumerate(categories):
        raw = cat.get("col_span")
        if raw is not None:
            spans.append(max(1, int(raw)))
        elif i < len(tpl):
            spans.append(max(1, int(tpl[i])))
        else:
            spans.append(1)
    return spans


def render_model_title_row(
    ws,
    *,
    row: int,
    col_start: int,
    col_end: int,
    title: str,
    styles: Sheet2PlaybookStyles,
) -> None:
    safe_write_merged_cell(
        ws,
        row=row,
        col=col_start,
        value=title,
        merge_cols=col_end - col_start + 1,
        font=styles.model_header_font,
        fill=styles.model_header_fill,
        alignment=styles.center_align,
    )
    ws.row_dimensions[row].height = 22


def render_element_playbook_block(
    ws,
    *,
    start_row: int,
    element_code: str,
    element_title: str,
    categories: List[Dict[str, Any]],
    styles: Sheet2PlaybookStyles,
    embed_image: Callable[..., None],
    img_cache: Dict[str, Any],
    matrix_cover_pixel_size: int,
    example_row_height: float,
    header_rows: int = 1,
) -> int:
    """写入单个要素块,返回下一可用行号(已含块后空行)."""
    if not categories:
        return start_row

    spans = compute_track_col_spans(element_code, categories)
    total_cols = sum(spans)
    col_track0 = 3  # C 列起为轨
    last_col = col_track0 + total_cols - 1

    header_row = start_row
    r_summary = header_row + header_rows
    r_expl = r_summary + 1
    r_ex = r_expl + 1
    block_bottom = r_ex

    # B 列: 表头行写要素名（原 A 列竖并已移至此处），后续行写行标签
    # A 列已收窄为 0.5（隐藏），不再写入
    safe_set_cell_value(
        ws,
        f"B{header_row}",
        element_title,
        font=styles.element_merged_font,
        fill=styles.element_merged_fill,
        alignment=styles.center_align,
    )
    for ri, lab in enumerate(("概括", "解释", "示例"), start=r_summary):
        safe_set_cell_value(
            ws,
            f"B{ri}",
            lab,
            font=styles.row_label_font,
            alignment=styles.center_align,
        )

    # 轨区: 表头 + 三行
    c0 = col_track0
    for i, cat in enumerate(categories):
        span = spans[i] if i < len(spans) else 1
        ratio = float(cat.get("ratio") or 0)
        ctype = str(cat.get("type") or "")
        tid = str(cat.get("track_id") or "").strip() or _default_track_id(
            element_code, i + 1
        )
        hdr = f"{tid} {ctype} {ratio:.0%}"
        safe_write_merged_cell(
            ws,
            row=header_row,
            col=c0,
            value=hdr,
            merge_cols=span,
            font=styles.rubric_header_font,
            fill=styles.rubric_header_fill,
            alignment=styles.center_align,
        )

        examples = cat.get("examples") or []
        summary = str(cat.get("summary") or "").strip() or f"{ctype} {ratio:.0%}"
        expl = str(cat.get("explanation") or "").strip()
        ex_text = str(cat.get("example_text") or "").strip()
        # 示例行文字：无 LLM 时用样本标题兜底(A_cover 可作画面说明,其余要素为可读字句)
        if not ex_text:
            titles: List[str] = []
            for ex in examples[:4]:
                if isinstance(ex, dict):
                    t = str(ex.get("title") or "").strip()
                    if t:
                        titles.append(t[:120])
            if titles:
                ex_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))

        safe_write_merged_cell(
            ws,
            row=r_summary,
            col=c0,
            value=summary,
            merge_cols=span,
            font=styles.data_font,
            alignment=styles.data_align,
        )
        safe_write_merged_cell(
            ws,
            row=r_expl,
            col=c0,
            value=expl,
            merge_cols=span,
            font=styles.data_font,
            alignment=styles.data_align,
        )
        safe_write_merged_cell(
            ws,
            row=r_ex,
            col=c0,
            value=ex_text,
            merge_cols=span,
            font=styles.data_font,
            alignment=styles.data_align,
        )

        urls: List[str] = []
        # 仅「封面图」要素嵌缩略图；其余要素示例为文字(见 example_text / 标题兜底)
        if element_code == "A_cover":
            for ex in examples[:2]:
                if isinstance(ex, dict):
                    u = (ex.get("cover_url") or "").strip()
                    if u:
                        urls.append(u)
        if urls:
            left_letter = get_column_letter(c0)
            embed_image(
                ws,
                r_ex,
                col_letter=left_letter,
                url=urls[0],
                cache=img_cache,
                pixel_size=matrix_cover_pixel_size,
            )
            if len(urls) > 1 and span >= 2:
                right_letter = get_column_letter(c0 + 1)
                embed_image(
                    ws,
                    r_ex,
                    col_letter=right_letter,
                    url=urls[1],
                    cache=img_cache,
                    pixel_size=matrix_cover_pixel_size,
                )

        c0 += span

    ws.row_dimensions[header_row].height = 24
    for rr in (r_summary, r_expl):
        ws.row_dimensions[rr].height = 36
    ws.row_dimensions[r_ex].height = example_row_height

    return block_bottom + 2  # 块后空 1 行


def _measure_col_content_width(ws, col_index: int) -> float:
    """扫描指定列所有可读单元格，返回内容自然显示宽度估算值。

    - 中文字符（ord > 127）计 2 个宽度单位，ASCII/数字计 1
    - wrap_text 情形按换行符切分，取各行最长行
    - 跳过 MergedCell（子格无独立内容，避免重复计算）
    """
    max_w: float = 0.0
    for row_cells in ws.iter_rows(min_col=col_index, max_col=col_index):
        for cell in row_cells:
            if isinstance(cell, MergedCell) or not cell.value:
                continue
            text = str(cell.value)
            for line in text.replace("\r", "").split("\n"):
                w: float = sum(2 if ord(c) > 127 else 1 for c in line.strip())
                if w > max_w:
                    max_w = w
    return max_w


def set_playbook_column_widths(
    ws,
    *,
    max_track_col: int,
    page_width_chars: float = 175.0,
    b_col_width: float = 12.0,
    min_track_width: float = 10.0,
    max_track_width: float = 50.0,
    # 保留旧参数签名以兼容旧调用，内部不再使用
    label_col_width: float = 10.0,
    row_label_col_width: float = 8.0,
    track_col_width: float = 14.0,
) -> None:
    """动态设置 Sheet2 playbook 各列列宽。

    策略：
    - A 列隐藏（宽 0.5），要素名已移至 B 列表头。
    - B 列固定 b_col_width（要素标题 + 概括/解释/示例 行标签）。
    - C 列起各轨列：
        1. 扫描实际内容，估算每列"自然宽度"（中文字符 ×2，ASCII ×1）
        2. 按比例分配 available = page_width_chars - b_col_width
        3. 各列受 [min_track_width, max_track_width] 约束
        4. 若因 min 下界总宽超出 available，等比缩回
        效果：总宽贴近 page_width_chars，内容多的轨列自动更宽。
    """
    ws.column_dimensions["A"].width = 11.0  # 空白列保持正常宽度
    ws.column_dimensions["B"].width = b_col_width

    num_tracks = max_track_col - 2  # 轨从 C(col=3) 起
    if num_tracks <= 0:
        return

    # 可用宽度 = 页面总宽 - A 列(11) - B 列，至少保证每轨达到 min_track_width
    available = max(float(num_tracks) * min_track_width, page_width_chars - 11.0 - b_col_width)

    # 测量各轨列内容自然宽度
    natural: Dict[int, float] = {}
    for ci in range(3, max_track_col + 1):
        natural[ci] = max(1.0, _measure_col_content_width(ws, ci))

    total_natural = sum(natural.values()) or float(num_tracks)

    # 按比例分配 available，受 min/max 约束
    raw: Dict[int, float] = {}
    for ci, nat in natural.items():
        alloc = (nat / total_natural) * available
        raw[ci] = max(min_track_width, min(max_track_width, alloc))

    # 若 min 下界导致总分配超出 available，等比缩回至 available
    total_raw = sum(raw.values())
    if total_raw > available:
        scale = available / total_raw
        for ci in raw:
            raw[ci] = max(min_track_width, raw[ci] * scale)

    for ci, w in raw.items():
        ws.column_dimensions[get_column_letter(ci)].width = round(w, 1)


# ---------------------------------------------------------------------------
# 动态行高
# ---------------------------------------------------------------------------

def _estimate_text_lines(text: str, col_width: float) -> int:
    """估算文字在指定列宽下的视觉行数。

    - 中文字符显示宽度 ×2，ASCII ×1
    - 按换行符先切段，每段再按列宽折行
    - col_width 单位与 openpyxl 列宽一致（等线 10pt 约 1 unit ≈ 1 ASCII 宽）
    """
    if not text:
        return 1
    display_width = max(col_width * 0.85, 1.0)  # 留少量边距
    total_lines = 0
    for para in text.replace("\r", "").split("\n"):
        char_w = sum(2 if ord(c) > 127 else 1 for c in para.strip())
        if char_w == 0:
            total_lines += 1
        else:
            total_lines += math.ceil(char_w / display_width)
    return max(1, total_lines)


def adjust_playbook_content_row_heights(ws) -> None:
    """动态调整 playbook 内容行（概括/解释/示例）的行高。

    在 set_playbook_column_widths() 之后调用，确保列宽已知。
    通过识别 B 列标签定位行类型，扫描 C 列起各轨列的最大内容行数，
    按 行数 × line_height + padding 计算并写入行高。
    """
    _LINE_HEIGHT = 14.5   # 等线 10pt 单行约 14.5pt
    _PADDING = 6.0        # 上下内边距
    _MIN_HEIGHTS: Dict[str, float] = {
        "概括": 28.0,
        "解释": 36.0,
        "示例": 50.0,
    }

    for row_idx in range(1, ws.max_row + 1):
        b_cell = ws.cell(row=row_idx, column=2)
        if isinstance(b_cell, MergedCell):
            continue
        label = str(b_cell.value or "").strip()
        if label not in _MIN_HEIGHTS:
            continue

        # 扫描该行 C 列起，找最多行数的格
        max_lines = 1
        for col_idx in range(3, (ws.max_column or 3) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            if isinstance(cell, MergedCell) or not cell.value:
                continue
            col_letter = get_column_letter(col_idx)
            col_w = ws.column_dimensions[col_letter].width or 14.0
            lines = _estimate_text_lines(str(cell.value), col_w)
            if lines > max_lines:
                max_lines = lines

        new_height = max(_MIN_HEIGHTS[label], max_lines * _LINE_HEIGHT + _PADDING)
        ws.row_dimensions[row_idx].height = round(new_height, 1)
