"""
Excel 导出主构造器(4.3pre.5)。

对齐 `docs/templates/kanglao_spec.md` 的 7 sheet 模板:
  1. 爆文总结                  — 双表(B-E 内容方向统计 / H-I 痛点统计)
  2. 爆文总结详情2              — 顶部汇总 + playbook(6 要素轨 × 概括/解释/示例 + 示例图)
  3. 数据源总                   — 20 列 + K 列封面图
  4. 竞品爆文                   — 22 列(多 T/U SEO/热词)
  5. 【品类】抗老精华互动top     — 22 列(多 B published_at)
  6. 【精华】小红书前10屏爆文   — 精简版
  7. 草稿                       — 复制 Sheet 2 骨架留空

顶层 build_excel_bytes() 是唯一公开入口。
"""

from __future__ import annotations

import asyncio
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from openpyxl import Workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ...domain.canvas import CanvasSchema
from ...domain.task_context import TaskContext
from ...infrastructure.repository import TaskRecord
from ...services.note_seo_extract import merge_seo_top10_with_multimodal_annotation
from .image_fetcher import CoverImageFetcher
from .openpyxl_helpers import (
    safe_int,
    safe_set_cell_style,
    safe_set_cell_value,
    safe_write_merged_cell,
)
from .sheet2_layout import (
    Sheet2PlaybookStyles,
    adjust_playbook_content_row_heights,
    render_element_playbook_block,
    render_model_title_row,
    set_playbook_column_widths,
)


# ------------------------------------------------------------------
# 常量与样式
# ------------------------------------------------------------------
_DEFAULT_STATS_AXIS_LABEL = "高频痛点 / 议程"

# 6 要素 code → 中文标签(对齐模板 Sheet 3 N-S 列顺序)
_ELEMENT_ORDER: List[Tuple[str, str]] = [
    ("A_cover", "封面"),
    ("B_cover_text", "封面压字"),
    ("C_title", "标题"),
    ("D_opening", "内容切入点"),
    ("E_product_intro", "产品引出方式"),
    ("F_product_placement", "产品植入方式"),
]

_ELEMENT_ANN_FIELD = {
    "A_cover": "cover_type",
    "B_cover_text": "cover_text_type",
    "C_title": "title_type",
    "D_opening": "opening_type",
    "E_product_intro": "product_intro_type",
    "F_product_placement": "product_placement_type",
}

_SOURCE_TYPE_LABEL_CN = {
    "category_top": "【品类】TOP",
    "competitor": "【竞品】爆文",
    "top_interaction": "【互动 TOP】",
}

# Sheet3 合并行写入：标记该行是「第几个池」首次纳入（去重后保留先出现的池），来源列只显示该池单一标签
_SHEET3_ROW_POOL_KEY = "__sheet3_row_pool"

_NON_ENUM_SUFFIX_RE = re.compile(
    r"\s*[（(][^）)]*(?:非枚举|自拟)[^）)]*[）)]\s*$"
)


def _format_note_source_cell(
    note: Dict[str, Any],
    *,
    sheet_primary_source: Optional[str] = None,
) -> str:
    """A 列「来源」文案。

    - 单来源 Sheet（4/5）：固定用该片对应标签。
    - 合并 Sheet3：优先用构建合并表时写入的 ``__sheet3_row_pool``（与 Sheet4/5 语义一致），
      避免把 ``sources_hit`` 里多标签用 `` / `` 拼成长串（易截断、且与「本行来自哪张分表」不一致）。
    """
    if sheet_primary_source:
        key = str(sheet_primary_source)
        return str(_SOURCE_TYPE_LABEL_CN.get(key, key))

    pool = note.get(_SHEET3_ROW_POOL_KEY)
    if pool is not None and str(pool).strip():
        pk = str(pool).strip()
        return str(_SOURCE_TYPE_LABEL_CN.get(pk, pk))


def _normalize_six_element_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # 只移除尾部类似“（非枚举，自拟）”的说明，不影响正常括号内容。
    while True:
        cleaned = _NON_ENUM_SUFFIX_RE.sub("", text).strip()
        if cleaned == text:
            return cleaned
        text = cleaned

    hits = note.get("sources_hit") or []
    if not isinstance(hits, list) or not hits:
        return ""
    # 兜底：旧数据无 pool 标记时只显示第一条（避免再拼接超长）
    return str(_SOURCE_TYPE_LABEL_CN.get(str(hits[0]), str(hits[0])))

# 样式常量
_HEADER_FONT = Font(bold=True, size=11, color="FFFFFF")
_HEADER_FILL = PatternFill(start_color="5A5550", end_color="5A5550", fill_type="solid")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

_TITLE_FONT = Font(bold=True, size=14)
_TITLE_ALIGN = Alignment(horizontal="center", vertical="center")

_DATA_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True)
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

# Sheet3-5 样本表:等线 10 号 + 单元格不换行(内容强制单行,换行符压成空格)
_SAMPLE_SHEET_FONT = Font(name="DengXian", size=10)
_SAMPLE_SHEET_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)
_SAMPLE_SHEET_HEADER_FONT = Font(name="DengXian", bold=True, size=10, color="FFFFFF")
_SAMPLE_SHEET_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)

_MODEL_HEADER_FILL = PatternFill(
    start_color="FFF0EE", end_color="FFF0EE", fill_type="solid"
)
_MODEL_HEADER_FONT = Font(name="等线", bold=True, size=10, color="FF4757")

_ELEMENT_HEADER_FILL = PatternFill(
    start_color="FFF8F0", end_color="FFF8F0", fill_type="solid"
)
_ELEMENT_HEADER_FONT = Font(bold=True, size=11, color="8B6914")

# Sheet2 playbook:黄底轨标题 + 灰底要素列(对齐业务模板)，全部使用等线 10 号
_S2_RUBRIC_HEADER_FILL = PatternFill(
    start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"
)
_S2_RUBRIC_HEADER_FONT = Font(name="等线", bold=True, size=10, color="000000")
_S2_ROW_LABEL_FONT = Font(name="等线", bold=True, size=10, color="5A5550")
_S2_ELEMENT_BLOCK_FILL = PatternFill(
    start_color="F2F2F2", end_color="F2F2F2", fill_type="solid"
)
_S2_ELEMENT_BLOCK_FONT = Font(name="等线", bold=True, size=10, color="1F4E79")
_S2_DATA_FONT = Font(name="等线", size=10)  # 概括/解释/示例内容行

# Sheet2 矩阵:含图数据行高 + K 列宽 + 示例图像素(与 Sheet3-5 小缩略图区分)
_COVER_ROW_HEIGHT = 80.0
_COVER_COLUMN_WIDTH = 18.0
_MATRIX_COVER_PIXEL_SIZE = 100
_SAMPLE_COVER_PIXEL_SIZE = 36  # 样本表行高 15 时配小图,避免溢出

# 全工作簿统一版式(保存前套用;豁免 Sheet1/2,见 _SKIP_DEFAULT_LAYOUT_SHEETS)
_DEFAULT_EXCEL_COL_WIDTH = 25
# Sheet3-5:类型/品牌/互动量/点赞/收藏/评论 在统一列宽后再收窄
_SAMPLE_METRIC_COL_WIDTH = 10.0
# Sheet4 竞品:T/U 热搜词与评论热词列需要更宽展示
_COMPETITOR_SEO_TU_COL_WIDTH = 60.0
_DEFAULT_EXCEL_ROW_HEIGHT = 15
_DEFAULT_EXCEL_CELL_ALIGN = Alignment(
    horizontal="center", vertical="center", wrap_text=False
)

# 不套用「列宽 25 / 行高 15 / 全居中」的 sheet(保留各 builder 内版式)
_SKIP_DEFAULT_LAYOUT_SHEETS = frozenset({"爆文总结", "爆文总结详情2"})


# ==================================================================
# 顶层入口
# ==================================================================
async def build_excel_bytes(
    *,
    task_record: TaskRecord,
    canvas: CanvasSchema,
    task_context: TaskContext,
    total_timeout: float = 60.0,
) -> bytes:
    """生成对齐 kanglao_spec.md 的 7 sheet xlsx 字节流。

    流程:
    1. 扫 4 分区收集所有封面 URL(含矩阵示例图)
    2. 用 asyncio.wait_for 限定 15s 并发下载封面
    3. 边构造 7 sheet 边嵌图
    4. 如果步骤 1-3 超过 total_timeout,返回"不带图"降级版本(仍合法 xlsx)

    Args:
        task_record: 任务元数据(owner / created_at 等)
        canvas: 已经渲染好的 CanvasSchema
        task_context: 运行时上下文(crawler/multimodal/viral/semantic 4 分区)
        total_timeout: 总超时(包含图片下载 + sheet 构造 + save)

    Returns:
        xlsx 文件字节流
    """
    crawler = task_context.get("crawler_output") or {}
    multimodal = task_context.get("multimodal_output") or {}
    viral_matrix = task_context.get("viral_model_output") or {}
    semantic = task_context.get("semantic_output") or {}
    annotations: Dict[str, Dict[str, Any]] = multimodal.get("annotations") or {}

    try:
        return await asyncio.wait_for(
            _build_full(
                task_record=task_record,
                canvas=canvas,
                crawler=crawler,
                annotations=annotations,
                viral_matrix=viral_matrix,
                semantic=semantic,
            ),
            timeout=total_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[excel_exporter] total_timeout={total_timeout}s 触发,降级为无图版本"
        )
        return _build_imageless(
            task_record=task_record,
            canvas=canvas,
            crawler=crawler,
            annotations=annotations,
            viral_matrix=viral_matrix,
            semantic=semantic,
        )


async def _build_full(
    *,
    task_record: TaskRecord,
    canvas: CanvasSchema,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
) -> bytes:
    urls = _collect_image_urls(viral_matrix=viral_matrix, crawler=crawler)
    img_cache: Dict[str, Optional[io.BytesIO]] = {}
    if urls:
        try:
            fetcher = CoverImageFetcher()
            img_cache = await asyncio.wait_for(fetcher.fetch_all(urls), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("[excel_exporter] 图片下载 15s 窗口超时,继续生成无图版本")
            img_cache = {}

    return await asyncio.to_thread(
        _assemble_workbook,
        task_record,
        canvas,
        crawler,
        annotations,
        viral_matrix,
        semantic,
        img_cache,
    )


def _task_keywords_for_export(
    task_record: TaskRecord, crawler: Dict[str, Any]
) -> List[str]:
    kws = [str(x).strip() for x in (task_record.keywords or []) if str(x).strip()]
    if kws:
        return kws[:8]
    ck = crawler.get("keywords")
    if isinstance(ck, list):
        return [str(x).strip() for x in ck if str(x).strip()][:8]
    return []


def _sheet5_title(task_keywords: List[str]) -> str:
    primary = str((task_keywords or ["互动"])[0]).strip() or "互动"
    # Excel sheet 名不能包含 []:*?/\
    cleaned = "".join("_" if ch in "[]:*?/\\"
                      else ch for ch in primary)
    title = f"【品类】{cleaned}互动top"
    return title[:31]


def _build_imageless(
    *,
    task_record: TaskRecord,
    canvas: CanvasSchema,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
) -> bytes:
    return _assemble_workbook(
        task_record,
        canvas,
        crawler,
        annotations,
        viral_matrix,
        semantic,
        {},
    )


def _assemble_workbook(
    task_record: TaskRecord,
    canvas: CanvasSchema,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
    img_cache: Dict[str, Optional[io.BytesIO]],
) -> bytes:
    """同步构造 7 sheet(openpyxl 本身是同步 API)。"""
    task_kw = _task_keywords_for_export(task_record, crawler)
    sheet5_title = _sheet5_title(task_kw)
    wb = Workbook()
    # 删除默认的 Sheet,改成具名 sheet
    default_ws = wb.active
    wb.remove(default_ws)

    ws1 = wb.create_sheet("爆文总结")
    _build_sheet1_summary(
        ws1,
        viral_matrix=viral_matrix,
        semantic=semantic,
        annotations=annotations,
    )

    ws2 = wb.create_sheet("爆文总结详情2")
    _build_sheet2_playbook(
        ws2,
        viral_matrix=viral_matrix,
        semantic=semantic,
        img_cache=img_cache,
    )

    ws3 = wb.create_sheet("数据源总")
    _build_sheet3_source(
        ws3,
        crawler=crawler,
        annotations=annotations,
        img_cache=img_cache,
        task_keywords=task_kw,
    )

    ws4 = wb.create_sheet("竞品爆文")
    _build_sheet4_competitor(
        ws4,
        crawler=crawler,
        annotations=annotations,
        img_cache=img_cache,
        task_keywords=task_kw,
    )

    ws5 = wb.create_sheet(sheet5_title)
    _build_sheet5_top_interaction(
        ws5,
        crawler=crawler,
        annotations=annotations,
        img_cache=img_cache,
        task_keywords=task_kw,
    )

    ws6 = wb.create_sheet("草稿")
    _build_sheet7_draft(ws6, semantic=semantic)

    # 元信息 docProps
    try:
        wb.properties.title = canvas.title or "爆文洞察与框架"
        wb.properties.creator = task_record.owner_user_id or "RedMuse"
    except Exception:
        pass

    _apply_default_excel_layout(wb)
    _apply_sample_sheet_metric_column_widths(wb, sheet5_title=sheet5_title)
    _apply_competitor_sheet_seo_wide_columns(wb)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ==================================================================
# URL 标准化（国际站 → 国内站，供业务人员直接点开）
# ==================================================================
def _to_xhs_url(url: str) -> str:
    """将笔记链接中的 rednote.com 替换为 xiaohongshu.com。

    采集时可能走国际站（webapi.rednote.com / www.rednote.com），
    但业务人员无法访问国际站，两个域名内容完全互通，直接替换即可。
    """
    if not url:
        return url
    return url.replace("www.rednote.com", "www.xiaohongshu.com") \
              .replace("webapi.rednote.com", "edith.xiaohongshu.com")


# ==================================================================
# 图片 URL 收集
# ==================================================================
def _collect_image_urls(
    *, viral_matrix: Dict[str, Any], crawler: Dict[str, Any]
) -> List[str]:
    """汇总所有 sheet 需要嵌入的图片 URL(去重由 fetcher 内部做)。"""
    urls: List[str] = []

    # Sheet 2: 每分类最多 2 张示例图
    for model in viral_matrix.get("models") or []:
        elements = model.get("elements") or {}
        for code, cats in elements.items():
            for cat in cats or []:
                examples = cat.get("examples") or []
                for ex in examples[:2]:
                    if isinstance(ex, dict):
                        url = (ex.get("cover_url") or "").strip()
                        if url:
                            urls.append(url)

    # Sheet 3-5: 样本封面
    sources = crawler.get("sources") or {}
    for src_notes in sources.values():
        for n in _as_note_list(src_notes):
            url = (n.get("cover_url") or "").strip()
            if url:
                urls.append(url)

    return urls


def _apply_default_excel_layout(wb: Workbook) -> None:
    """列宽 25、行高 15、单元格水平垂直居中(跳过 MergedCell;Sheet1/2 除外)。"""
    for ws in wb.worksheets:
        if ws.title in _SKIP_DEFAULT_LAYOUT_SHEETS:
            continue
        max_row = ws.max_row or 1
        max_col = ws.max_column or 1
        for col_idx in range(1, max_col + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = float(
                _DEFAULT_EXCEL_COL_WIDTH
            )
        for row_idx in range(1, max_row + 1):
            ws.row_dimensions[row_idx].height = float(_DEFAULT_EXCEL_ROW_HEIGHT)
        for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                cell.alignment = _DEFAULT_EXCEL_CELL_ALIGN


def _as_note_list(entry: Any) -> List[Dict[str, Any]]:
    if isinstance(entry, list):
        return [n for n in entry if isinstance(n, dict)]
    if isinstance(entry, dict):
        inner = entry.get("notes")
        if isinstance(inner, list):
            return [n for n in inner if isinstance(n, dict)]
    return []


def _extract_axis_noun(stats_axis_label: str) -> str:
    """从轴名提取最后2字类型词，用于 J 列表头。

    示例: "选香痛点" → "痛点", "皮肤问题" → "问题",
         "高频痛点 / 议程" → "痛点", "喂养议程" → "议程"
    """
    label = (stats_axis_label or "").split("/")[0].split("／")[0].strip()
    noun = label[-2:] if len(label) >= 2 else label
    return noun if noun else "痛点"


def _write_viral_top_summary_tables(
    ws,
    *,
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
    header_row: int = 1,
    include_insight_cols: bool = False,
) -> Tuple[int, int]:
    """与 Sheet1 顶部一致:左表 B-E + 右表 H-I(-J-K) + unused 的 F 列标记。

    H: stats_axis_label（关键词）
    I: 出现次数
    J: {axis_noun}本质定义（仅 include_insight_cols=True 时写入，即 Sheet1）
    K: 核心诉求阐释（同上）

    Returns:
        (row, pain_row):与现有 Sheet1 相同语义 — 左/右各自「下一空行」下标。
    """
    models = viral_matrix.get("models") or []
    total_sample = safe_int(viral_matrix.get("total_sample_count"))
    unused = viral_matrix.get("unused_directions") or []

    stats_axis_label = (
        semantic.get("stats_axis_label") or _DEFAULT_STATS_AXIS_LABEL
    ).strip() or _DEFAULT_STATS_AXIS_LABEL
    pain_items = semantic.get("pain_points_top") or []
    axis_noun = _extract_axis_noun(stats_axis_label)

    hr = header_row
    safe_set_cell_value(
        ws, f"B{hr}", "内容方向",
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    safe_set_cell_value(
        ws, f"C{hr}", "数量",
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    safe_set_cell_value(
        ws, f"D{hr}", "占比",
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    safe_set_cell_value(
        ws, f"E{hr}", "均互动",
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    safe_set_cell_value(
        ws, f"H{hr}", stats_axis_label,
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    safe_set_cell_value(
        ws, f"I{hr}", "出现次数",
        font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
    )
    if include_insight_cols:
        safe_set_cell_value(
            ws, f"J{hr}", f"{axis_noun}本质定义",
            font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
        )
        safe_set_cell_value(
            ws, f"K{hr}", "核心诉求阐释",
            font=_HEADER_FONT, fill=_HEADER_FILL, alignment=_HEADER_ALIGN,
        )

    row = hr + 1
    divisor = total_sample if total_sample > 0 else 1
    for model in models:
        name = str(model.get("name") or "")
        count = safe_int(
            round(float(model.get("coverage") or 0) * total_sample)
        )
        avg_interaction = safe_int(model.get("avg_interaction"))
        safe_set_cell_value(ws, f"B{row}", name, alignment=_DATA_ALIGN)
        safe_set_cell_value(ws, f"C{row}", count, alignment=_CENTER_ALIGN)
        safe_set_cell_value(
            ws, f"D{row}", f"=C{row}/{divisor}", alignment=_CENTER_ALIGN
        )
        ws[f"D{row}"].number_format = "0.0%"
        safe_set_cell_value(ws, f"E{row}", avg_interaction, alignment=_CENTER_ALIGN)
        row += 1

    for ud in unused:
        direction = str(ud.get("direction") or "")
        ratio = float(ud.get("ratio") or 0)
        count = safe_int(round(ratio * total_sample))
        avg_interaction = safe_int(ud.get("avg_interaction"))
        safe_set_cell_value(ws, f"B{row}", direction, alignment=_DATA_ALIGN)
        safe_set_cell_value(ws, f"C{row}", count, alignment=_CENTER_ALIGN)
        safe_set_cell_value(
            ws, f"D{row}", f"=C{row}/{divisor}", alignment=_CENTER_ALIGN
        )
        ws[f"D{row}"].number_format = "0.0%"
        safe_set_cell_value(ws, f"E{row}", avg_interaction, alignment=_CENTER_ALIGN)
        safe_set_cell_value(
            ws, f"F{row}", "*不做",
            alignment=_CENTER_ALIGN,
            font=Font(size=10, color="A8A4A0"),
        )
        row += 1

    pain_row = hr + 1
    for item in pain_items:
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "")
            count = safe_int(item.get("count"))
        else:
            keyword = str(item)
            count = 0
        if not keyword:
            continue
        safe_set_cell_value(ws, f"H{pain_row}", keyword, alignment=_DATA_ALIGN)
        safe_set_cell_value(ws, f"I{pain_row}", count, alignment=_CENTER_ALIGN)
        if include_insight_cols:
            # J 列: 本质定义（essence_definition，可选，旧数据留空）
            essence = str(item.get("essence_definition") or "") if isinstance(item, dict) else ""
            if essence:
                safe_set_cell_value(
                    ws, f"J{pain_row}", essence,
                    alignment=Alignment(horizontal="left", vertical="center", wrap_text=True),
                )
            # K 列: 核心诉求阐释（core_appeal，可选，旧数据留空）
            appeal = str(item.get("core_appeal") or "") if isinstance(item, dict) else ""
            if appeal:
                safe_set_cell_value(
                    ws, f"K{pain_row}", appeal,
                    alignment=Alignment(horizontal="left", vertical="center", wrap_text=True),
                )
            if essence or appeal:
                ws.row_dimensions[pain_row].height = 40
        pain_row += 1

    return row, pain_row


# ==================================================================
# Sheet 1: 爆文总结(双表)
# ==================================================================
def _build_sheet1_summary(
    ws,
    *,
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
) -> None:
    """左表 B-E: 内容方向 / 数量 / 占比 / 均互动
    右表 H-I: {stats_axis_label} / 出现次数
    """
    row, pain_row = _write_viral_top_summary_tables(
        ws, viral_matrix=viral_matrix, semantic=semantic, header_row=1,
        include_insight_cols=True,
    )
    ws.row_dimensions[1].height = 24

    models = viral_matrix.get("models") or []
    pain_items = semantic.get("pain_points_top") or []
    stats_axis_label = (
        semantic.get("stats_axis_label") or _DEFAULT_STATS_AXIS_LABEL
    ).strip() or _DEFAULT_STATS_AXIS_LABEL

    # ---- 区域 2: 爆文模型 × 6 要素分解(对齐模板行 12+) ----
    model_start = max(row, pain_row) + 2

    # 样式: 模型标题行(浅蓝底)
    _s1_model_header_fill = PatternFill(
        start_color="D6E4F0", end_color="D6E4F0", fill_type="solid"
    )
    _s1_model_header_font = Font(bold=True, size=11, color="1F4E79")
    _s1_element_font = Font(bold=True, size=10, color="5A5550")
    _s1_element_fill = PatternFill(
        start_color="F2F2F2", end_color="F2F2F2", fill_type="solid"
    )
    # 样式: 模型定义行(米灰底)
    _s1_defn_label_font = Font(bold=True, size=10, color="5A5550")
    _s1_defn_label_fill = PatternFill(
        start_color="EBEBDA", end_color="EBEBDA", fill_type="solid"
    )
    _s1_defn_text_fill = PatternFill(
        start_color="EBEBDA", end_color="EBEBDA", fill_type="solid"
    )
    _s1_defn_text_font = Font(size=10, color="2C4770")
    _s1_defn_align = Alignment(
        horizontal="left", vertical="center", wrap_text=True
    )

    # 预计算全局痛点关键词集合(模型痛点必须为此集合的子集)
    global_pain_set = set()
    for p in pain_items:
        if isinstance(p, dict):
            kw = str(p.get("keyword") or "").strip()
        else:
            kw = str(p).strip()
        if kw:
            global_pain_set.add(kw)

    cur = model_start
    for idx, model in enumerate(models, start=1):
        model_id = str(model.get("model_id") or f"M{idx}")
        name = str(model.get("name") or "")
        model_label = f"爆文模型{idx} {name}"
        definition = str(model.get("definition") or "")

        # ---- 聚合该模型的痛点(全局痛点的子集,按模型笔记重新计数) ----
        model_pain_counter: Dict[str, int] = {}
        for nid in (model.get("sample_note_ids") or []):
            ann = annotations.get(str(nid)) or {}
            pk = ann.get("pain_keywords") or ""
            if isinstance(pk, list):
                kw_list = [str(k).strip() for k in pk if str(k).strip()]
            elif isinstance(pk, str) and pk.strip():
                # 对齐 InsightAgent._split_tokens: 统一 /、;；|，\n → ,
                normed = (pk.replace("/", ",").replace("、", ",")
                          .replace(";", ",").replace("；", ",")
                          .replace("|", ",").replace("，", ",")
                          .replace("\n", ","))
                kw_list = [k.strip() for k in normed.split(",") if k.strip()]
            else:
                kw_list = []
            for kw_str in kw_list:
                if kw_str in global_pain_set:
                    model_pain_counter[kw_str] = model_pain_counter.get(kw_str, 0) + 1
        model_pain_sorted = sorted(
            model_pain_counter.items(), key=lambda x: x[1], reverse=True
        )

        # ---- 模型 header: B:C 合并 + D 标"备注" + H 标"痛点" + I 标"出现次数" ----
        safe_write_merged_cell(
            ws,
            row=cur,
            col=2,
            value=model_label,
            merge_cols=2,
            font=_s1_model_header_font,
            fill=_s1_model_header_fill,
            alignment=_CENTER_ALIGN,
        )
        safe_set_cell_value(
            ws, f"D{cur}", "备注",
            font=_s1_model_header_font, fill=_s1_model_header_fill,
            alignment=_CENTER_ALIGN,
        )
        safe_set_cell_value(
            ws, f"H{cur}", stats_axis_label,
            font=_s1_model_header_font, fill=_s1_model_header_fill,
            alignment=_CENTER_ALIGN,
        )
        safe_set_cell_value(
            ws, f"I{cur}", "出现次数",
            font=_s1_model_header_font, fill=_s1_model_header_fill,
            alignment=_CENTER_ALIGN,
        )
        ws.row_dimensions[cur].height = 22
        cur += 1
        # 右侧痛点从 header 下一行开始，不受左侧定义行偏移影响
        pain_write_row = cur

        # ---- 模型定义行(属加种差法): B=标签, C-D 合并=定义文本 ----
        if definition:
            safe_set_cell_value(
                ws, f"B{cur}", "模型定义",
                font=_s1_defn_label_font, fill=_s1_defn_label_fill,
                alignment=_CENTER_ALIGN,
            )
            safe_write_merged_cell(
                ws,
                row=cur,
                col=3,
                value=definition,
                merge_cols=2,
                font=_s1_defn_text_font,
                fill=_s1_defn_text_fill,
                alignment=_s1_defn_align,
            )
            ws.row_dimensions[cur].height = 55
            cur += 1

        # ---- 逐要素输出 ----
        elements = model.get("elements") or {}
        for code, label in _ELEMENT_ORDER:
            cats = elements.get(code) or []
            if not cats:
                continue
            element_start = cur
            for ci, cat in enumerate(cats):
                cat_type = str(cat.get("type") or "")
                # C 列: 子类名 (如 "A1 博主与产品合照")
                safe_set_cell_value(ws, f"C{cur}", cat_type, alignment=_DATA_ALIGN)
                # D 列: 备注 (来自 cat 的 note 或者留空)
                note_text = str(cat.get("note") or cat.get("description") or "")
                if note_text:
                    safe_set_cell_value(ws, f"D{cur}", note_text, alignment=_DATA_ALIGN)
                cur += 1

            # B 列合并 = 要素名(如 "A封面")
            span = cur - element_start
            if span >= 1:
                safe_write_merged_cell(
                    ws,
                    row=element_start,
                    col=2,
                    value=f"{code[0]}{label}",
                    merge_rows=span,
                    font=_s1_element_font,
                    fill=_s1_element_fill,
                    alignment=_CENTER_ALIGN,
                )

        # ---- H-I 列: 该模型的痛点 Top 排列在模型数据行右侧 ----
        for pi, (pk_word, pk_cnt) in enumerate(model_pain_sorted):
            r = pain_write_row + pi
            safe_set_cell_value(ws, f"H{r}", pk_word, alignment=_DATA_ALIGN)
            safe_set_cell_value(ws, f"I{r}", pk_cnt, alignment=_CENTER_ALIGN)

        # 痛点行可能长于左侧要素块,须取较大行号再下移,否则下一模型表头会盖住 H/I
        pain_end_row = pain_write_row + len(model_pain_sorted)
        cur = max(cur, pain_end_row)
        # 模型间空 1 行
        cur += 1

    # Sheet1 不随全表统一版式:表头行高 + 各列宽(与模板手工表一致)
    ws.row_dimensions[1].height = 24
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 42
    ws.column_dimensions["D"].width = 35
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 3
    ws.column_dimensions["G"].width = 3
    ws.column_dimensions["H"].width = 22
    ws.column_dimensions["I"].width = 12
    ws.column_dimensions["J"].width = 45
    ws.column_dimensions["K"].width = 50


# ==================================================================
# Sheet 2: 爆文总结详情2(playbook 矩阵)
# ==================================================================
def _model_playbook_last_col(model: Dict[str, Any]) -> int:
    """该模型下各要素轨区最大尾列号(含 A/B 标签列)."""
    from .sheet2_layout import compute_track_col_spans

    last = 8
    elements = model.get("elements") or {}
    for code, _lbl in _ELEMENT_ORDER:
        cats = elements.get(code) or []
        if not cats:
            continue
        spans = compute_track_col_spans(code, cats)
        end_c = 2 + sum(spans)  # 轨自第 3 列起 → 尾列 = 2 + sum(spans)
        last = max(last, end_c)
    return last


def _build_sheet2_playbook(
    ws,
    *,
    viral_matrix: Dict[str, Any],
    semantic: Dict[str, Any],
    img_cache: Dict[str, Optional[io.BytesIO]],
) -> None:
    """顶部同 Sheet1 汇总表 + 爆文模型 playbook(6 要素 × 轨 × 概括/解释/示例 + 示例图)。"""
    models = viral_matrix.get("models") or []
    row, pain_row = _write_viral_top_summary_tables(
        ws, viral_matrix=viral_matrix, semantic=semantic, header_row=1
    )
    ws.row_dimensions[1].height = 24

    styles = Sheet2PlaybookStyles(
        model_header_font=_MODEL_HEADER_FONT,
        model_header_fill=_MODEL_HEADER_FILL,
        rubric_header_font=_S2_RUBRIC_HEADER_FONT,
        rubric_header_fill=_S2_RUBRIC_HEADER_FILL,
        row_label_font=_S2_ROW_LABEL_FONT,
        element_merged_font=_S2_ELEMENT_BLOCK_FONT,
        element_merged_fill=_S2_ELEMENT_BLOCK_FILL,
        data_font=_S2_DATA_FONT,
        center_align=_CENTER_ALIGN,
        data_align=_DATA_ALIGN,
    )

    cur = max(row, pain_row) + 2
    max_track_col = 8
    for idx, model in enumerate(models, start=1):
        model_id = str(model.get("model_id") or f"M{idx}")
        name = str(model.get("name") or "")
        coverage = float(model.get("coverage") or 0) * 100
        model_title = f"爆文模型{idx} {model_id} {name} (coverage {coverage:.1f}%)"

        end_col = _model_playbook_last_col(model)
        max_track_col = max(max_track_col, end_col)

        render_model_title_row(
            ws,
            row=cur,
            col_start=2,  # B 列起（A 已收窄隐藏）
            col_end=end_col,
            title=model_title,
            styles=styles,
        )
        cur += 1

        elements = model.get("elements") or {}
        for code, label in _ELEMENT_ORDER:
            cats = elements.get(code) or []
            if not cats:
                continue
            element_title = f"{code[0]}{label}"
            cur = render_element_playbook_block(
                ws,
                start_row=cur,
                element_code=code,
                element_title=element_title,
                categories=cats,
                styles=styles,
                embed_image=_try_embed_image,
                img_cache=img_cache,
                matrix_cover_pixel_size=_MATRIX_COVER_PIXEL_SIZE,
                example_row_height=_COVER_ROW_HEIGHT,
            )

    set_playbook_column_widths(ws, max_track_col=max_track_col)
    adjust_playbook_content_row_heights(ws)


def _as_single_line_cell_value(val: Any) -> Any:
    """去掉换行,合并为单行文本(Excel 样本表不换行展示)。"""
    if not isinstance(val, str):
        return val
    parts = [p.strip() for p in val.replace("\r", "").split("\n") if p.strip()]
    return " ".join(parts) if parts else ""


# ------------------------------------------------------------------
# 样本表「品牌」列:标题/正文/标签词表匹配 + 爬虫检索词回退(非 LLM,导出侧推断)
# ------------------------------------------------------------------
_GENERIC_TOPIC_KEYWORDS: frozenset[str] = frozenset(
    {
        "手机", "智能手机", "安卓", "苹果机", "巧克力", "甜品", "零食", "护肤", "护肤品",
        "美妆", "化妆", "抗老", "抗老精华", "精华", "面霜", "乳液", "面膜", "口红", "唇膏",
        "防晒", "卸妆", "洗发水", "洗发", "防脱", "母婴", "家居", "穿搭", "减肥", "健身",
        "运动", "数码", "好物", "推荐", "测评", "开箱", "种草", "平价", "大牌", "国货",
        "显卡", "笔记本", "耳机", "蓝牙", "相机",
    }
)

# 常见品牌/产品线(越长越优先匹配);可随业务再扩充
_RAW_BRAND_LEXICON: frozenset[str] = frozenset(
    {
        "IPSA", "茵芙莎", "PMPM", "SK-II", "林清轩", "玉兰油", "OLAY", "雅诗兰黛",
        "兰蔻", "欧莱雅", "科颜氏", "倩碧", "资生堂", "雪花秀", "Whoo", "悦诗风吟",
        "完美日记", "花西子", "HBN", "珀莱雅", "薇诺娜", "润百颜", "可复美", "敷尔佳",
        "美迪惠尔", "理肤泉", "雅漾", "依云", "贝德玛", "妮维雅", "曼秀雷敦", "露得清",
        "海蓝之谜", "La Mer", "赫莲娜", "修丽可", "娇韵诗", "迪奥", "Dior", "香奈儿",
        "Chanel", "圣罗兰", "YSL", "阿玛尼", "Giorgio Armani", "纪梵希", "Tom Ford",
        "祖玛珑", "Jo Malone", "欧舒丹", "施华蔻", "卡诗", "潘婷", "海飞丝", "飘柔",
        "舒肤佳", "护舒宝", "帮宝适", "好奇", "Fazer", "费列罗", "德芙", "Dove",
        "士力架", "M&M", "好时", "雀巢", "Nestle", "奥利奥", "乐事", "百事", "可口可乐",
        "元气森林", "农夫山泉", "怡宝", "蒙牛", "伊利", "光明", "安慕希", "特仑苏",
        "iPhone", "iPad", "MacBook", "AirPods", "Apple", "苹果", "华为", "HUAWEI",
        "小米", "Xiaomi", "红米", "Redmi", "vivo", "OPPO", "一加", "OnePlus", "荣耀",
        "honor", "realme", "iQOO", "魅族", "三星", "Sony", "索尼", "尼康", "佳能",
        "大疆", "DJI", "联想", "Lenovo", "戴尔", "Dell", "惠普", "HP", "华硕", "ASUS",
        "微软", "Microsoft", "罗技", "Logitech", "耐克", "Nike", "阿迪达斯", "Adidas",
        "李宁", "安踏", "特步", "361度", "匹克", "优衣库", "无印良品", "MUJI", "ZARA",
        "H&M", "宜家", "IKEA", "戴森", "Dyson", "飞利浦", "Philips", "松下", "Panasonic",
        "博世", "Bosch", "西门子", "美的", "格力", "海尔", "海信", "TCL", "创维",
        "每日黑巧", "食验室",
    }
)
_BRAND_NAMES_SORTED_DESC: Tuple[str, ...] = tuple(
    sorted(_RAW_BRAND_LEXICON, key=len, reverse=True)
)


def _infer_brand_for_export(
    note: Dict[str, Any],
    ann: Dict[str, Any],
    *,
    task_keywords: List[str],
) -> str:
    """导出 Excel 时填充「品牌」列:多模态 brand 字段优先,否则正文词表匹配,再爬虫 keyword / 任务词。"""
    ann_brand = str(ann.get("brand") or "").strip()
    if ann_brand:
        return ann_brand[:80]
    title = str(note.get("title") or "")
    desc = str(note.get("desc") or "")[:1200]
    tags = note.get("tags") or []
    if isinstance(tags, list):
        tag_txt = " ".join(str(t) for t in tags if t)
    else:
        tag_txt = str(tags or "")
    blob = f"{title} {desc} {tag_txt}".strip()
    if not blob:
        blob = title
    blob_l = blob.lower()
    for cand in _BRAND_NAMES_SORTED_DESC:
        if len(cand) < 2:
            continue
        if cand.lower() in blob_l:
            return cand[:80]
    kw = str(note.get("keyword") or "").strip()
    gen_l = {x.lower() for x in _GENERIC_TOPIC_KEYWORDS}
    if 2 <= len(kw) <= 24 and kw.lower() not in gen_l and kw not in _GENERIC_TOPIC_KEYWORDS:
        return kw[:80]
    for tk in task_keywords:
        s = str(tk).strip()
        if not s or len(s) > 32:
            continue
        if s.lower() in gen_l or s in _GENERIC_TOPIC_KEYWORDS:
            continue
        if s in title:
            return s[:80]
    return ""


# ==================================================================
# Sheet 3-5: 三样本表(同型构造器 + 特化)
# ==================================================================
def _sample_header_row(
    ws,
    headers: List[str],
    *,
    cover_col_letter: str = "K",
) -> None:
    for idx, h in enumerate(headers, start=1):
        cell_ref = f"{get_column_letter(idx)}1"
        safe_set_cell_value(
            ws,
            cell_ref,
            h,
            font=_SAMPLE_SHEET_HEADER_FONT,
            fill=_HEADER_FILL,
            alignment=_SAMPLE_SHEET_HEADER_ALIGN,
        )
    _ = cover_col_letter  # 列宽由 Sheet3-7 的 _apply_default_excel_layout 统一为 25


def _sample_write_row(
    ws,
    row: int,
    note: Dict[str, Any],
    ann: Dict[str, Any],
    *,
    columns: List[str],
    img_cache: Dict[str, Optional[io.BytesIO]],
    cover_col: str = "K",
    task_keywords: Optional[List[str]] = None,
    sheet_primary_source: Optional[str] = None,
) -> None:
    """按 columns 描述把一条 note 的 A-S (+T/U) 列填入。

    封面/六要素/痛点/内容方向等列来自 ``multimodal_output.annotations[note_id]``;
    若该笔记未参与多模态标注或模型解析失败,对应列会为空(非「接口取不到」爬虫字段)。

    columns 元素为列用途 key,支持:
      source / author / note_type / brand / url /
      interaction_formula / likes / collects / comments / direction /
      cover / title / pain_keywords /
      cover_type / cover_text_type / title_type / opening_type / product_intro_type / product_placement_type /
      seo_top10 / comment_hotwords_top10 / published_at
    """
    for col_idx, key in enumerate(columns, start=1):
        letter = get_column_letter(col_idx)
        cell_ref = f"{letter}{row}"
        value: Any = ""

        if key == "source":
            value = _format_note_source_cell(
                note, sheet_primary_source=sheet_primary_source
            )
        elif key == "author":
            value = str(note.get("nickname") or "")
        elif key == "note_type":
            nt = note.get("note_type") or note.get("media_type") or ""
            value = "视频" if nt in ("video", "视频") else "图集"
        elif key == "brand":
            value = _infer_brand_for_export(
                note, ann, task_keywords=task_keywords or []
            )
        elif key == "url":
            value = _to_xhs_url(str(note.get("url") or note.get("note_url") or ""))
        elif key == "interaction_formula":
            # =G+H+I 三列合计,需要依赖 likes/collects/comments 所在列
            # 用 Python 预先算出固定值,避免依赖列位置
            value = safe_int(note.get("interaction_score")) or (
                safe_int(note.get("likes"))
                + safe_int(note.get("collects"))
                + safe_int(note.get("comments"))
            )
        elif key == "likes":
            value = safe_int(note.get("likes"))
        elif key == "collects":
            value = safe_int(note.get("collects"))
        elif key == "comments":
            value = safe_int(note.get("comments"))
        elif key == "direction":
            value = str(ann.get("content_direction") or "")
        elif key == "cover":
            # 嵌图占位(实际在末尾 _try_embed_image 处理)
            _try_embed_image(
                ws,
                row,
                col_letter=letter,
                url=str(note.get("cover_url") or "").strip(),
                cache=img_cache,
                pixel_size=_SAMPLE_COVER_PIXEL_SIZE,
            )
            continue
        elif key == "title":
            value = str(note.get("title") or "")
        elif key == "pain_keywords":
            value = str(ann.get("pain_keywords") or note.get("pain_keywords") or "")
        elif key in _ELEMENT_ANN_FIELD.values():
            # 6 要素任何一个都走 ann 里相应字段
            value = _normalize_six_element_label(ann.get(key))
        elif key == "seo_top10":
            merged = merge_seo_top10_with_multimodal_annotation(note, ann)
            value = ",".join(str(x) for x in merged if x)
        elif key == "comment_hotwords_top10":
            arr = note.get("comment_hotwords_top10") or []
            value = ",".join(str(x) for x in arr if x) if isinstance(arr, list) else ""
        elif key == "published_at":
            value = str(note.get("published_at") or "")

        value = _as_single_line_cell_value(value)
        safe_set_cell_value(
            ws,
            cell_ref,
            value,
            alignment=_SAMPLE_SHEET_ALIGN,
            font=_SAMPLE_SHEET_FONT,
        )

    # 行高由 Sheet3-5 的 _apply_default_excel_layout 统一为 15


# Sheet 3 数据源总: 20 列 A-S
_SHEET3_HEADERS = [
    "来源",         # A
    "达人昵称",     # B
    "类型",         # C
    "品牌",         # D
    "笔记链接",     # E
    "互动量",       # F
    "点赞",         # G
    "收藏",         # H
    "评论",         # I
    "内容方向",     # J
    "封面截图",     # K (嵌图)
    "标题",         # L
    "内容关键词/痛点", # M
    "封面",         # N
    "封面压字",     # O
    "标题(类型)",   # P
    "内容切入点",   # Q
    "产品引出方式", # R
    "产品植入方式", # S
]
_SHEET3_COLS = [
    "source", "author", "note_type", "brand", "url",
    "interaction_formula", "likes", "collects", "comments", "direction",
    "cover", "title", "pain_keywords",
    "cover_type", "cover_text_type", "title_type", "opening_type",
    "product_intro_type", "product_placement_type",
]


def _build_sheet3_source(
    ws,
    *,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    img_cache: Dict[str, Optional[io.BytesIO]],
    task_keywords: Optional[List[str]] = None,
) -> None:
    sources = crawler.get("sources") or {}
    # 合并两个来源: 竞品爆文 + 本品样本(top_interaction), 按 note_id 去重
    seen_ids: set[str] = set()
    merged_notes: List[Dict[str, Any]] = []
    for source_key in ("competitor", "top_interaction"):
        for n in _as_note_list(sources.get(source_key)):
            nid = str(n.get("note_id") or "")
            if nid and nid in seen_ids:
                continue
            if nid:
                seen_ids.add(nid)
            row = dict(n)
            row[_SHEET3_ROW_POOL_KEY] = source_key
            merged_notes.append(row)
    _render_sample_sheet(
        ws, merged_notes,
        headers=_SHEET3_HEADERS, columns=_SHEET3_COLS,
        annotations=annotations, img_cache=img_cache,
        task_keywords=task_keywords,
    )


# Sheet 4 竞品爆文: 22 列(在 Sheet3 基础上加 T/U)
_SHEET4_HEADERS = _SHEET3_HEADERS + [
    "笔记涵盖热搜词 Top10",
    "评论热词 Top10",
]
_SHEET4_COLS = _SHEET3_COLS + ["seo_top10", "comment_hotwords_top10"]


def _build_sheet4_competitor(
    ws,
    *,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    img_cache: Dict[str, Optional[io.BytesIO]],
    task_keywords: Optional[List[str]] = None,
) -> None:
    sources = crawler.get("sources") or {}
    notes = _as_note_list(sources.get("competitor"))
    _render_sample_sheet(
        ws, notes,
        headers=_SHEET4_HEADERS, columns=_SHEET4_COLS,
        annotations=annotations, img_cache=img_cache,
        task_keywords=task_keywords,
        sheet_primary_source="competitor",
    )


# Sheet 5 品类互动 top: 与 Sheet 3 相似 + 一列 published_at 放在 B
_SHEET5_HEADERS = [
    "来源",             # A
    "笔记发布时间",     # B (published_at)
    "达人昵称",         # C
    "类型",             # D
    "品牌",             # E
    "笔记链接",         # F
    "互动量",           # G
    "点赞",             # H
    "收藏",             # I
    "评论",             # J
    "内容方向",         # K
    "封面截图",         # L (嵌图)
    "标题",             # M
    "封面",             # N
    "封面压字",         # O
    "标题(类型)",       # P
    "内容切入点",       # Q
    "产品引出方式",     # R
    "产品植入方式",     # S
]
_SHEET5_COLS = [
    "source", "published_at", "author", "note_type", "brand", "url",
    "interaction_formula", "likes", "collects", "comments", "direction",
    "cover", "title",
    "cover_type", "cover_text_type", "title_type", "opening_type",
    "product_intro_type", "product_placement_type",
]


def _build_sheet5_top_interaction(
    ws,
    *,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    img_cache: Dict[str, Optional[io.BytesIO]],
    task_keywords: Optional[List[str]] = None,
) -> None:
    sources = crawler.get("sources") or {}
    notes = _as_note_list(sources.get("top_interaction"))
    _render_sample_sheet(
        ws, notes,
        headers=_SHEET5_HEADERS, columns=_SHEET5_COLS,
        annotations=annotations, img_cache=img_cache,
        cover_col="L",
        task_keywords=task_keywords,
        sheet_primary_source="top_interaction",
    )



_SAMPLE_METRIC_COL_KEYS = frozenset(
    {
        "note_type",
        "brand",
        "interaction_formula",
        "likes",
        "collects",
        "comments",
    }
)


def _apply_sample_sheet_metric_column_widths(
    wb: Workbook, *, sheet5_title: str
) -> None:
    """在 `_apply_default_excel_layout` 之后,将样本表指定逻辑列收窄为 10。"""
    specs: Tuple[Tuple[str, List[str]], ...] = (
        ("数据源总", _SHEET3_COLS),
        ("竞品爆文", _SHEET4_COLS),
        (sheet5_title, _SHEET5_COLS),
    )
    for title, col_keys in specs:
        ws = wb[title]
        for idx, key in enumerate(col_keys, start=1):
            if key in _SAMPLE_METRIC_COL_KEYS:
                ws.column_dimensions[get_column_letter(idx)].width = float(
                    _SAMPLE_METRIC_COL_WIDTH
                )


def _apply_competitor_sheet_seo_wide_columns(wb: Workbook) -> None:
    """竞品爆文 Sheet:T/U(笔记涵盖热搜词 Top10 / 评论热词 Top10)加宽。"""
    ws = wb["竞品爆文"]
    ws.column_dimensions["T"].width = float(_COMPETITOR_SEO_TU_COL_WIDTH)
    ws.column_dimensions["U"].width = float(_COMPETITOR_SEO_TU_COL_WIDTH)


def _render_sample_sheet(
    ws,
    notes: List[Dict[str, Any]],
    *,
    headers: List[str],
    columns: List[str],
    annotations: Dict[str, Dict[str, Any]],
    img_cache: Dict[str, Optional[io.BytesIO]],
    cover_col: str = "K",
    task_keywords: Optional[List[str]] = None,
    sheet_primary_source: Optional[str] = None,
) -> None:
    _sample_header_row(ws, headers, cover_col_letter=cover_col)
    if not notes:
        # 空样本时写一个占位行
        safe_set_cell_value(
            ws,
            "A2",
            "(暂无样本)",
            alignment=_SAMPLE_SHEET_ALIGN,
            font=Font(name="DengXian", italic=True, size=10, color="A8A4A0"),
        )
        return

    for idx, note in enumerate(notes, start=2):
        note_id = str(note.get("note_id") or "")
        ann = annotations.get(note_id) or {}
        _sample_write_row(
            ws,
            row=idx,
            note=note,
            ann=ann,
            columns=columns,
            img_cache=img_cache,
            cover_col=cover_col,
            task_keywords=task_keywords,
            sheet_primary_source=sheet_primary_source,
        )


# ==================================================================
# Sheet 7: 空白草稿(与 Sheet 2 同引擎:顶部汇总表 + playbook 占位)
# ==================================================================
def _build_sheet7_draft(
    ws,
    *,
    semantic: Optional[Dict[str, Any]] = None,
) -> None:
    """顶部汇总表头与 Sheet2 一致;playbook 区 1 个模型 × 6 要素 × 单轨「待填」占位。"""
    sem = dict(semantic or {})
    if "stats_axis_label" not in sem:
        sem["stats_axis_label"] = _DEFAULT_STATS_AXIS_LABEL
    if "pain_points_top" not in sem:
        sem["pain_points_top"] = []

    empty_vm: Dict[str, Any] = {
        "models": [],
        "unused_directions": [],
        "total_sample_count": 0,
    }
    row, pain_row = _write_viral_top_summary_tables(
        ws, viral_matrix=empty_vm, semantic=sem, header_row=1
    )
    ws.row_dimensions[1].height = 24

    styles = Sheet2PlaybookStyles(
        model_header_font=_MODEL_HEADER_FONT,
        model_header_fill=_MODEL_HEADER_FILL,
        rubric_header_font=_S2_RUBRIC_HEADER_FONT,
        rubric_header_fill=_S2_RUBRIC_HEADER_FILL,
        row_label_font=_S2_ROW_LABEL_FONT,
        element_merged_font=_S2_ELEMENT_BLOCK_FONT,
        element_merged_fill=_S2_ELEMENT_BLOCK_FILL,
        data_font=_S2_DATA_FONT,
        center_align=_CENTER_ALIGN,
        data_align=_DATA_ALIGN,
    )

    cur = max(row, pain_row) + 2
    draft_note = (
        "创作草稿:请在上表填写方向统计,在下表按要素轨补充概括/解释/示例与代表图"
    )
    safe_write_merged_cell(
        ws,
        row=cur,
        col=1,
        value=draft_note,
        merge_cols=8,
        font=_TITLE_FONT,
        alignment=_TITLE_ALIGN,
    )
    cur += 1

    placeholder_model: Dict[str, Any] = {
        "model_id": "M1",
        "name": "(待填模型名)",
        "coverage": 0.0,
        "avg_interaction": 0,
        "elements": {
            code: [
                {
                    "type": "(待填)",
                    "ratio": 0.0,
                    "count": 0,
                    "examples": [],
                }
            ]
            for code, _ in _ELEMENT_ORDER
        },
    }
    end_col = _model_playbook_last_col(placeholder_model)
    render_model_title_row(
        ws,
        row=cur,
        col_start=2,  # B 列起（A 已收窄隐藏）
        col_end=end_col,
        title="爆文模型1 M1 (待填模型名) (coverage 0.0%)",
        styles=styles,
    )
    cur += 1
    for code, label in _ELEMENT_ORDER:
        cats = placeholder_model["elements"][code]
        element_title = f"{code[0]}{label}"
        cur = render_element_playbook_block(
            ws,
            start_row=cur,
            element_code=code,
            element_title=element_title,
            categories=cats,
            styles=styles,
            embed_image=_try_embed_image,
            img_cache={},
            matrix_cover_pixel_size=_MATRIX_COVER_PIXEL_SIZE,
            example_row_height=_COVER_ROW_HEIGHT,
        )

    set_playbook_column_widths(ws, max_track_col=max(end_col, 8))
    adjust_playbook_content_row_heights(ws)


# ==================================================================
# 图片嵌入
# ==================================================================
def _try_embed_image(
    ws,
    row: int,
    *,
    col_letter: str,
    url: str,
    cache: Dict[str, Optional[io.BytesIO]],
    pixel_size: Optional[int] = None,
) -> None:
    """尝试把 cache[url] 嵌入 ws 的 (row, col_letter) cell。

    失败(缓存 miss / BytesIO 为 None / XLImage 构造失败)时静默跳过,
    cell 自然留空(符合决策 #4A)。
    """
    if not url:
        return
    stream = cache.get(url)
    if stream is None:
        return
    try:
        # openpyxl 的 Image 会吃掉 BytesIO 的内容;多处 cell 复用同一 BytesIO
        # 需要先复制一份 bytes 再包新 BytesIO
        stream.seek(0)
        raw = stream.read()
        stream.seek(0)

        px = pixel_size if pixel_size is not None else _MATRIX_COVER_PIXEL_SIZE
        img = XLImage(io.BytesIO(raw))
        img.width = px
        img.height = px
        anchor = f"{col_letter}{row}"
        ws.add_image(img, anchor)
    except Exception as exc:  # noqa: BLE001 - 嵌图失败 cell 留空即可
        logger.debug(f"[excel_exporter] 嵌图失败 anchor={col_letter}{row} err={exc}")


__all__ = ["build_excel_bytes"]
