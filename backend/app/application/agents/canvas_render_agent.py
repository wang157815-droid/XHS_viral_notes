"""
CanvasRenderAgent: 按 spec v1.2.2 整合各 Agent 输出为新画布模块(阶段 4.3pre.3;
品类 TOP 不入画布样本层)。

**整体改动**(相比 4.3pre.2):
- 旧 11 模块硬下线(strategy 4 / insight 3 / knowledge 1 / image 1 / video 1 / crawler-sample 1)
- 新画布模块直接对齐 `docs/canvas_restructure_spec.md` v1.2.2(品类 TOP 仅参与采集与总览,
  不单独出画布样本卡;与 Excel「数据源总」三源合并一致):
    1. mod-overview-stats            Sheet 1 统计总览
    2. mod-viral-model-matrix       Sheet 2 爆文模型矩阵(核心,含双级 paragraph_id)
    3–5/3–8. Layer3 样本表:见 `_build_layer3_sample_modules`（高级配置「不限」时三源×图文/视频共 6 卡；
       仅图文/仅视频时仍为 3 卡,与 Excel 导出无关,导出仍读 crawler.sources 全量）。
    6. mod-seo-insights              Sheet 4 T/U 聚合
    7. mod-pain-points               Sheet 1 右侧表(stats_axis_label 参数化)
    8. mod-draft-workbench           Sheet 7 空白工作台(CanvasRenderAgent 静态骨架)

**paragraph_id 规则**:
- mod-viral-model-matrix 两级: `M{i}` / `M{i}-{CODE}-C{j}`(由 ViralModelAgent 写入)
- mod-pain-points: `P{i}`
- mod-seo-insights: `S-core-{i}` / `S-long-{i}`
- 样本类模块: `{module_id}-note-{note_id}`
- mod-overview-stats / mod-draft-workbench: 整块不支持细粒度反馈,不生成 paragraph_id

**不再消费**: `strategy_output` 分区 / 旧 `semantic_output.industry/competitor/brand`。
InsightAgent 4.3pre.3 已改为直接产出 `content_direction / pain_points_top / seo_aggregation / stats_axis_label`。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ...domain.canvas import (
    CanvasModule,
    CanvasModuleAction,
    CanvasSchema,
    build_empty_canvas,
)
from ...domain.module_status import ModuleStatus
from ...domain.module_graph import ModuleNodeSpec, module_graph
from ...domain.task_context import TaskContextWriter
from ...services.note_seo_extract import merge_seo_top10_with_multimodal_annotation
from ..task_service import task_service
from .base import AgentContext, AgentResult, BaseAgent


# ============================================================
# 模块依赖图(Canvas 8 新节点 + mod-draft-workbench 静态骨架)
# ============================================================
_MODULE_REGISTRATIONS: List[ModuleNodeSpec] = [
    # 样本 / 总览层(CrawlerAgent 驱动,无上游;品类 TOP 不入画布样本模块)
    ModuleNodeSpec(module_id="mod-overview-stats", provides_by="CrawlerAgent"),
    ModuleNodeSpec(module_id="mod-competitor-samples", provides_by="CrawlerAgent"),
    ModuleNodeSpec(module_id="mod-top-interaction-samples", provides_by="CrawlerAgent"),
    ModuleNodeSpec(module_id="mod-serp-top-samples", provides_by="CrawlerAgent"),

    # 矩阵层(ViralModelAgent 驱动,消费 multimodal.annotations + crawler.all_notes)
    ModuleNodeSpec(
        module_id="mod-viral-model-matrix",
        provides_by="ViralModelAgent",
        depends_on=[
            "mod-competitor-samples",
            "mod-top-interaction-samples",
            "mod-serp-top-samples",
        ],
    ),

    # 洞察层(InsightAgent 驱动)
    ModuleNodeSpec(
        module_id="mod-pain-points",
        provides_by="InsightAgent",
        depends_on=["mod-viral-model-matrix"],
    ),
    ModuleNodeSpec(
        module_id="mod-seo-insights",
        provides_by="InsightAgent",
        # 优先依赖竞品样本；无竞品时也可由 top_interaction / serp_top 驱动
        depends_on=["mod-viral-model-matrix"],
    ),

    # 空白工作台(CanvasRenderAgent 静态骨架)
    ModuleNodeSpec(module_id="mod-draft-workbench", provides_by="CanvasRenderAgent"),
]

module_graph.register_many(_MODULE_REGISTRATIONS)


# ============================================================
# 常量
# ============================================================
_DEFAULT_STATS_AXIS_LABEL = "高频痛点 / 议程"
_SAMPLE_ROW_LIMIT = 50        # 样本表每模块最多展示行数

# Layer3 拆卡:与 CrawlerAgent advanced_config.note_type 一致 — 0 不限 / 1 视频 / 2 图文
_LAYER3_SPLIT_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("mod-competitor-samples-image", "样本 · 竞品爆文 · 图文", "competitor", "image"),
    ("mod-competitor-samples-video", "样本 · 竞品爆文 · 视频", "competitor", "video"),
    (
        "mod-top-interaction-samples-image",
        "样本 · 互动 TOP · 图文",
        "top_interaction",
        "image",
    ),
    (
        "mod-top-interaction-samples-video",
        "样本 · 互动 TOP · 视频",
        "top_interaction",
        "video",
    ),
    ("mod-serp-top-samples-image", "样本 · SERP 前 10 屏 · 图文", "serp_top", "image"),
    ("mod-serp-top-samples-video", "样本 · SERP 前 10 屏 · 视频", "serp_top", "video"),
)

_DRAFT_SKELETON_FIELDS = [
    "title",
    "cover_concept",
    "hook",
    "structure",
    "product_intro",
    "cta",
]


def _actions(commands: List[str]) -> List[CanvasModuleAction]:
    label_map = {
        "regenerate": "重新生成",
        "regenerate_cascade": "级联重生",
        "delete": "删除",
        "restore": "恢复",
        "export": "导出",
    }
    return [
        CanvasModuleAction(id=cmd, label=label_map.get(cmd, cmd), command=cmd)  # type: ignore[arg-type]
        for cmd in commands
    ]


def _status_from(payload: Any) -> ModuleStatus:
    """只要上游分区非空(或是非空列表/字典)就标 READY,否则 PENDING。"""
    if payload is None:
        return ModuleStatus.PENDING
    if isinstance(payload, (list, dict, tuple, set)) and not payload:
        return ModuleStatus.PENDING
    return ModuleStatus.READY


# ============================================================
# note 归一化(样本类模块共用)
# ============================================================
def _shape_sample_note(
    note: Dict[str, Any],
    *,
    module_id: str,
    annotations: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """把 crawler_output 里一条 note 归一化为画布样本模块行 schema。

    字段对齐 spec §5.1(20 基础 + 2 扩展):样本表只展示最常用字段,
    导出层(4.3pre.5 excel_exporter)再读原始数据补 N-S 列。
    """
    note_id = str(note.get("note_id") or "")
    ann = annotations.get(note_id) or {}
    likes = int(note.get("likes") or 0)
    collects = int(note.get("collects") or 0)
    comments = int(note.get("comments") or 0)
    interaction_total = int(note.get("interaction_score") or (likes + collects + comments))

    row: Dict[str, Any] = {
        "note_id": note_id,
        "title": (note.get("title") or "").strip(),
        "author": (note.get("nickname") or "").strip(),
        "likes": likes,
        "collects": collects,
        "comments": comments,
        "interaction_total": interaction_total,
        "cover_url": (note.get("cover_url") or "").strip(),
        "note_url": (note.get("url") or "").strip(),
        "is_image": (note.get("media_type") == "image"),
        "content_direction": (ann.get("content_direction") or "").strip(),
        "pain_keywords": (ann.get("pain_keywords") or note.get("pain_keywords") or "").strip(),
        "sources_hit": list(note.get("sources_hit") or []),
        # 扩展字段(竞品特有)
        "comment_hotwords_top10": list(note.get("comment_hotwords_top10") or []),
        "seo_top10": merge_seo_top10_with_multimodal_annotation(note, ann),
        # paragraph_id 单条笔记级
        "paragraph_id": f"{module_id}-note-{note_id}" if note_id else None,
    }
    # 清理空 paragraph_id,避免前端误渲染
    if not row["paragraph_id"]:
        row.pop("paragraph_id", None)
    return row


# ============================================================
# 各模块构造器
# ============================================================

def _build_overview_stats(
    crawler: Dict[str, Any],
    input_spec: Dict[str, Any],
    *,
    annotations: Dict[str, Dict[str, Any]],
) -> CanvasModule:
    """Sheet 1 左表等价:总览 + 来源分布(不含右侧痛点表,那个在 mod-pain-points)。

    来源分布与 Excel 样本分表 / 数据源总合并语义一致,只展示三源:
    竞品 / 互动 TOP / SERP(品类 industry 池仍参与采集与 all_notes,此处不单独计数)。
    """
    all_notes = list(crawler.get("all_notes") or [])
    sources = crawler.get("sources") or {}
    image_count = sum(1 for n in all_notes if n.get("media_type") == "image")
    video_count = sum(1 for n in all_notes if n.get("media_type") == "video")

    source_breakdown = [
        {"source_type": src, "count": len(_as_note_list(sources.get(src)))}
        for src in ("competitor", "top_interaction", "serp_top")
    ]

    direction_counter: Counter = Counter()
    for note_id, ann in annotations.items():
        d = (ann.get("content_direction") or "").strip()
        if d:
            direction_counter[d] += 1
    direction_breakdown = [
        {"direction": k, "count": v} for k, v in direction_counter.most_common(10)
    ]

    keyword = ""
    kws = crawler.get("keywords") or []
    if isinstance(kws, list) and kws:
        keyword = str(kws[0])
    else:
        keyword = str(input_spec.get("raw_input") or "")

    content = {
        "total_notes": len(all_notes),
        "image_count": image_count,
        "video_count": video_count,
        "source_breakdown": source_breakdown,
        "direction_breakdown": direction_breakdown,
        "sample_date_range": "",   # 4.3pre.3 暂不计算(源数据多数无 published_at)
        "keyword": keyword,
    }

    status = _status_from(all_notes)
    return CanvasModule(
        module_id="mod-overview-stats",
        title="统计总览",
        layer=1,
        status=status,
        version=1,
        highlighted=True,
        default_expanded=True,
        summary=(
            f"{len(all_notes)} 条样本 · 图文 {image_count} / 视频 {video_count}"
        ),
        content=content,
        actions=_actions(["export"]),
        depends_on=[],
    )


def _build_viral_model_matrix(
    viral_matrix: Dict[str, Any],
    *,
    stats_axis_label: str,
    sample_modules_dep: Optional[List[str]] = None,
) -> CanvasModule:
    """Sheet 2 核心矩阵:直接嵌入 ViralModelMatrix.to_dict()(已带双级 paragraph_id)。"""
    models = list(viral_matrix.get("models") or [])
    # 注意: models 为空列表时 _status_from 会得到 PENDING,会误显示「待生成」。
    # ViralModelAgent 跑完后一定会写入 models 键(可为空数组),此时应标 READY 并带出 empty_reason。
    if "models" in viral_matrix:
        status = ModuleStatus.READY
    else:
        status = ModuleStatus.PENDING

    empty_reason = str(viral_matrix.get("empty_reason") or "").strip()
    empty_reason_summary = {
        "no_annotations": "矩阵为空:无多模态标注(图文/视频 6 要素未产出或校验未通过)",
        "all_below_threshold": "矩阵为空:内容方向过于分散,未达占比阈值",
    }

    content = {
        "matrix": {
            "models": models,
            "unused_directions": list(viral_matrix.get("unused_directions") or []),
            "total_sample_count": int(viral_matrix.get("total_sample_count") or 0),
            "taxonomy_version": str(viral_matrix.get("taxonomy_version") or ""),
            "stats_axis_label": stats_axis_label,
            "empty_reason": empty_reason,
        },
    }
    if models:
        summary = (
            f"{len(models)} 个爆文模型 · "
            f"{len(viral_matrix.get('unused_directions') or [])} 个「不做」方向"
        )
    elif empty_reason:
        summary = empty_reason_summary.get(
            empty_reason, f"爆文模型矩阵已生成(空): {empty_reason}"
        )
    else:
        summary = "爆文模型矩阵(待样本聚类)"
    dep = sample_modules_dep or [
        "mod-competitor-samples",
        "mod-top-interaction-samples",
        "mod-serp-top-samples",
    ]
    return CanvasModule(
        module_id="mod-viral-model-matrix",
        title="爆文模型矩阵",
        layer=1,
        status=status,
        version=1,
        highlighted=True,
        default_expanded=True,
        summary=summary,
        content=content,
            actions=_actions(["regen_sheet2_narrative", "rename_models", "regenerate", "regenerate_cascade", "export"]),
        depends_on=list(dep),
    )


def _build_sample_module(
    *,
    module_id: str,
    title: str,
    source_type: str,
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    source_key: str,
    layer: int = 3,
    default_expanded: bool = False,
    notes_override: Optional[List[Dict[str, Any]]] = None,
    media_kind: Optional[str] = None,
) -> CanvasModule:
    """样本类模块统一构造器(category_top / competitor / top_interaction / serp_top)。"""
    sources = crawler.get("sources") or {}
    if notes_override is not None:
        raw_notes = list(notes_override)[:_SAMPLE_ROW_LIMIT]
    else:
        raw_notes = _as_note_list(sources.get(source_key))[:_SAMPLE_ROW_LIMIT]
    rows = [
        _shape_sample_note(n, module_id=module_id, annotations=annotations)
        for n in raw_notes
    ]
    status = _status_from(rows)
    content: Dict[str, Any] = {
        "source_type": source_type,
        "sample_count": len(rows),
        "notes": rows,
    }
    if media_kind:
        content["media_kind"] = media_kind
    return CanvasModule(
        module_id=module_id,
        title=title,
        layer=layer,  # type: ignore[arg-type]
        status=status,
        version=1,
        default_expanded=default_expanded,
        summary=f"{len(rows)} 条样本",
        content=content,
        actions=_actions(["export"]),
        depends_on=[],
    )


def _build_pain_points(semantic: Dict[str, Any]) -> CanvasModule:
    """Sheet 1 右侧「高频痛点 / 议程 Top」:items 带 count 和 paragraph_id,轴名参数化。"""
    raw_items = list(semantic.get("pain_points_top") or [])
    items: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            kw = str(item.get("keyword") or "").strip()
            cnt = int(item.get("count") or 0)
        else:
            # 兼容极老旧 List[str] 形态(不应再出现,防御性)
            kw = str(item).strip()
            cnt = 0
        if not kw:
            continue
        items.append({"keyword": kw, "count": cnt, "paragraph_id": f"P{idx}"})

    stats_axis_label = str(
        semantic.get("stats_axis_label") or _DEFAULT_STATS_AXIS_LABEL
    ).strip() or _DEFAULT_STATS_AXIS_LABEL

    content = {
        "stats_axis_label": stats_axis_label,
        "items": items,
    }
    status = _status_from(items)
    return CanvasModule(
        module_id="mod-pain-points",
        title=f"{stats_axis_label} Top",
        layer=2,
        status=status,
        version=1,
        summary=f"共 {len(items)} 条{stats_axis_label}条目",
        content=content,
        actions=_actions(["regenerate"]),
        depends_on=["mod-viral-model-matrix"],
    )


def _build_seo_insights(
    semantic: Dict[str, Any],
    *,
    competitor_depends_on: Optional[List[str]] = None,
) -> CanvasModule:
    """Sheet 4 T/U 聚合:core_keywords + long_tail,分别带 paragraph_id。"""
    seo = semantic.get("seo_aggregation") or {}
    core_raw = list(seo.get("core_keywords") or [])
    long_raw = list(seo.get("long_tail") or [])
    advice = str(seo.get("differentiation_advice") or "").strip()

    def _shape(items: List[Any], prefix: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for idx, item in enumerate(items, start=1):
            if isinstance(item, dict):
                kw = str(item.get("keyword") or "").strip()
                cnt = int(item.get("count") or 0)
            else:
                kw = str(item).strip()
                cnt = 0
            if not kw:
                continue
            out.append({"keyword": kw, "count": cnt, "paragraph_id": f"{prefix}-{idx}"})
        return out

    core = _shape(core_raw, "S-core")
    long_tail = _shape(long_raw, "S-long")

    content = {
        "core_keywords": core,
        "long_tail": long_tail,
        "differentiation_advice": advice,
    }
    status = _status_from(core) if core else _status_from(long_tail)
    summary = (
        f"核心词 {len(core)} / 长尾 {len(long_tail)}"
        if (core or long_tail)
        else "SEO 洞察(暂无数据)"
    )
    # depends_on 优先使用调用方传入的竞品模块；无竞品时回退到 top_interaction
    comp_dep: List[str] = list(competitor_depends_on) if competitor_depends_on else []
    if not comp_dep:
        comp_dep = ["mod-top-interaction-samples"]
    return CanvasModule(
        module_id="mod-seo-insights",
        title="SEO 关键词洞察",
        layer=2,
        status=status,
        version=1,
        summary=summary,
        content=content,
        actions=_actions(["regenerate"]),
        depends_on=comp_dep,
    )


def _build_draft_workbench() -> CanvasModule:
    """Sheet 7 空白工作台:blank_skeleton,用户可编辑的 6 字段骨架。

    决策 #3A: 首版不自动预填内容,前端渲染为可编辑空白表。
    """
    fields = {k: "" for k in _DRAFT_SKELETON_FIELDS}
    content = {
        "template": "blank_skeleton",
        "fields": fields,
    }
    return CanvasModule(
        module_id="mod-draft-workbench",
        title="创作草稿区",
        layer=2,
        status=ModuleStatus.READY,
        version=1,
        default_expanded=False,
        summary="可编辑的创作草稿骨架(6 字段)",
        content=content,
        actions=_actions(["restore", "export"]),
        depends_on=[],
    )


# ============================================================
# helper: 兼容扁平 list 与 {"notes": [...]} 两种 sources 结构
# ============================================================
def _as_note_list(entry: Any) -> List[Dict[str, Any]]:
    if isinstance(entry, list):
        return [n for n in entry if isinstance(n, dict)]
    if isinstance(entry, dict):
        inner = entry.get("notes")
        if isinstance(inner, list):
            return [n for n in inner if isinstance(n, dict)]
    return []


def _canvas_note_type_int(input_spec: Dict[str, Any]) -> int:
    """与 CrawlerAgent._parse_advanced_config 的 note_type 语义对齐:0 不限 /1 视频 /2 图文。"""
    ac = input_spec.get("advanced_config")
    if not isinstance(ac, dict):
        return 0
    raw = ac.get("note_type")
    note_type_map = {"不限": 0, "视频": 1, "图文": 2}
    if isinstance(raw, str) and raw in note_type_map:
        return int(note_type_map[raw])
    return 0


def _filter_notes_by_media(notes: List[Dict[str, Any]], media: str) -> List[Dict[str, Any]]:
    media = (media or "image").strip()
    out: List[Dict[str, Any]] = []
    for n in notes:
        mt = str(n.get("media_type") or "image").strip()
        if mt == media:
            out.append(n)
    return out


def _build_layer3_sample_modules(
    crawler: Dict[str, Any],
    annotations: Dict[str, Dict[str, Any]],
    input_spec: Dict[str, Any],
) -> Tuple[List[CanvasModule], List[str], List[str]]:
    """返回 (样本模块列表, 矩阵 depends_on 列表, SEO 竞品 depends_on 列表)。

    SEO 竞品 depends_on 列表在无竞品词时为空，_build_seo_insights 会
    自动回退到 mod-top-interaction-samples 作为依赖占位。
    """
    nt = _canvas_note_type_int(input_spec)
    sources = crawler.get("sources") or {}
    modules: List[CanvasModule] = []
    matrix_dep: List[str] = []
    seo_comp_dep: List[str] = []

    if nt == 0:
        for mid, title, source_key, media in _LAYER3_SPLIT_SPECS:
            raw = _as_note_list(sources.get(source_key))
            filtered = _filter_notes_by_media(raw, media)
            modules.append(
                _build_sample_module(
                    module_id=mid,
                    title=title,
                    source_type=source_key,
                    crawler=crawler,
                    annotations=annotations,
                    source_key=source_key,
                    notes_override=filtered,
                    media_kind=media,
                )
            )
            matrix_dep.append(mid)
            if source_key == "competitor":
                seo_comp_dep.append(mid)
    else:
        media = "video" if nt == 1 else "image"
        triple = (
            (
                "mod-competitor-samples",
                "样本 · 竞品爆文",
                "competitor",
            ),
            (
                "mod-top-interaction-samples",
                "样本 · 互动 TOP",
                "top_interaction",
            ),
            (
                "mod-serp-top-samples",
                "样本 · SERP 前 10 屏",
                "serp_top",
            ),
        )
        for mid, title, source_key in triple:
            raw = _as_note_list(sources.get(source_key))
            filtered = _filter_notes_by_media(raw, media)
            modules.append(
                _build_sample_module(
                    module_id=mid,
                    title=title,
                    source_type=source_key,
                    crawler=crawler,
                    annotations=annotations,
                    source_key=source_key,
                    notes_override=filtered,
                    media_kind=media,
                )
            )
            matrix_dep.append(mid)
            if source_key == "competitor":
                seo_comp_dep.append(mid)

    return modules, matrix_dep, seo_comp_dep


# ============================================================
# 主装配
# ============================================================
def _build_modules(
    crawler: Dict[str, Any],
    semantic: Dict[str, Any],
    viral_matrix: Dict[str, Any],
    multimodal: Dict[str, Any],
    input_spec: Dict[str, Any],
) -> List[CanvasModule]:
    annotations: Dict[str, Dict[str, Any]] = dict(
        (multimodal.get("annotations") or {})
    )
    stats_axis_label = str(
        semantic.get("stats_axis_label") or _DEFAULT_STATS_AXIS_LABEL
    ).strip() or _DEFAULT_STATS_AXIS_LABEL

    modules: List[CanvasModule] = []

    # 1. 统计总览(Sheet 1 左)
    modules.append(
        _build_overview_stats(crawler, input_spec, annotations=annotations)
    )

    layer3_samples, matrix_sample_dep, seo_comp_dep = _build_layer3_sample_modules(
        crawler, annotations, input_spec
    )

    # 2. 爆文模型矩阵(Sheet 2 核心)
    modules.append(
        _build_viral_model_matrix(
            viral_matrix,
            stats_axis_label=stats_axis_label,
            sample_modules_dep=matrix_sample_dep,
        )
    )

    # 3–8. Layer3 样本表(不限=6 卡图文/视频×三源;仅图文/视频=3 卡过滤展示)
    modules.extend(layer3_samples)

    # 7. SEO 关键词洞察
    modules.append(
        _build_seo_insights(semantic, competitor_depends_on=seo_comp_dep)
    )

    # 8. 高频痛点/议程 Top(stats_axis_label 参数化)
    modules.append(_build_pain_points(semantic))

    # 9. 草稿区(静态骨架)
    modules.append(_build_draft_workbench())

    return modules


# ============================================================
# Agent 本体
# ============================================================
class CanvasRenderAgent(BaseAgent):
    agent_id = "CanvasRenderAgent"
    # CanvasRenderAgent 只产出 draft-workbench 这一个静态模块(其余是各 Agent 产出,
    # 但 `provides` 语义在 module_graph 由各上游 Agent 负责,这里保持空以免重复)。
    provides: List[str] = []
    # 新拓扑依赖数据分区级别,而非画布模块 id(更稳定,不会被旧 module_id 漂移影响)
    depends_on = [
        "mod-competitor-samples",
        "mod-competitor-samples-image",
        "mod-competitor-samples-video",
        "mod-top-interaction-samples",
        "mod-top-interaction-samples-image",
        "mod-top-interaction-samples-video",
        "mod-serp-top-samples",
        "mod-serp-top-samples-image",
        "mod-serp-top-samples-video",
        "mod-viral-model-matrix",
        "mod-pain-points",
        "mod-seo-insights",
    ]
    write_partition = "canvas_document"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        ctx = context.task_context
        crawler = ctx.get("crawler_output") or {}
        semantic = ctx.get("semantic_output") or {}
        viral_matrix = ctx.get("viral_model_output") or {}
        multimodal = ctx.get("multimodal_output") or {}
        input_spec = ctx.get("input_spec") or {}

        await self.emit_progress(task_id, "画布渲染:整合模块(含三源样本)", progress=90)

        raw_input = (input_spec.get("raw_input") or "").strip()
        # 标题只显示本品词（brand 维度第一个词，或 keywords 第一个词，兜底用 raw_input 前20字）
        brand_kws = (input_spec.get("dimensions") or {}).get("brand") or input_spec.get("keywords") or []
        title = str(brand_kws[0]).strip() if brand_kws else (raw_input[:20] or task_id)

        sample_count = int(crawler.get("sample_count") or 0)
        keywords = crawler.get("keywords") or []
        keywords_disp = ",".join(str(k) for k in keywords)[:40] or "-"
        source = crawler.get("source") or "-"
        base_canvas: CanvasSchema = build_empty_canvas(
            task_id=task_id,
            title=title,
            subtitle=(
                f"{sample_count} 篇样本 · 来源 {source} · 关键词 {keywords_disp}"
            ),
        )
        base_canvas.modules = _build_modules(
            crawler=crawler,
            semantic=semantic,
            viral_matrix=viral_matrix,
            multimodal=multimodal,
            input_spec=input_spec,
        )

        canvas = task_service.set_canvas(task_id, base_canvas)

        TaskContextWriter(ctx).write(
            "canvas_document",
            canvas.to_dict(),
            agent_id=self.agent_id,
            note="render_phase4_3pre3",
        )

        from ...domain.events import TaskEventType

        await self._bus.publish_event(
            task_id=task_id,
            type=TaskEventType.CANVAS_SCHEMA_UPDATED,
            payload=canvas.to_dict(),
        )

        return AgentResult(
            ok=True,
            produced_modules=[m.module_id for m in canvas.modules],
            output={
                "canvas_version": canvas.canvas_version,
                "module_count": len(canvas.modules),
            },
        )
