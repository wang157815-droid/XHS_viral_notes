
"""
CrawlerAgent：三维采集 + 图文/视频分离（阶段 4.1 真实化 / hotfix 1）。

阶段 4.1 hotfix 1 重要改动（解决 XHS 软反爬返回空结果）：
- 按关键词去重：多个业务维度 keywords 完全相同时，只真实采集一次，结果共享给同组维度。
- 不同 keyword 组串行执行而非并行：同 IP + 同 cookies 并发请求极易触发风控并返回
  `success=True, items=[]`，串行执行以时间换稳定性。
- 降低默认 target_count 到 15（env 可覆盖）。
- 增强调试日志：打印维度分组、每组首条结果标题，便于排查。

输出 `crawler_output`：
  {
    "source": "live" | "stub",
    "sample_count": int,
    "keywords": [...],
    "dimensions_keywords": {"industry": [...], "competitor": [...], "brand": [...]},
    "samples_by_dimension": {"industry": [...], "competitor": [...], "brand": [...]},
    "notes_image": [...],
    "notes_video": [...],
    "note_summary": [...],   # 兼容旧字段
    "dimension_status": {"industry": "ok" | "empty" | "failed" | "shared:<dim>" ...}
  }

Redis L1 缓存 / pgvector L2 预热留给阶段 4.x。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

from loguru import logger

from ...domain.task_context import TaskContextWriter
from ...infrastructure.repository import task_repository
from .base import AgentContext, AgentResult, BaseAgent


_DIMENSIONS = ("industry", "competitor", "brand")
_PER_GROUP_TIMEOUT = int(os.getenv("CRAWLER_GROUP_TIMEOUT", "3600"))
# 每组（共享同一组关键词的维度）的目标笔记数。
# 小红书搜索 API 每页 20 条，内部按三种 sort 维度分配（点赞 / 评论 / 收藏），
# 所以 target=45 表示每个 sort 大约 15 条，基本是一页左右且不浪费。
# 若要更多可调到 100+，但耗时会线性上升（每页 20-40s，100 条约 2-3 分钟）。
# 注意：_PER_GROUP_TIMEOUT 仅作极端防挂起保护网，正常采集不会触发：
#   - 默认 45 条：约 1-2 分钟内完成
#   - 500 条（前端最大）：约 30-40 分钟
#   - 设为 3600s(1h) 确保任意合理配置都能采集完毕，不在中途斩断数据
_DEFAULT_TARGET_PER_GROUP = int(os.getenv("CRAWLER_TARGET_PER_GROUP", "45"))
_MIN_SAMPLE = int(os.getenv("CRAWLER_MIN_SAMPLE", "5"))
_INTER_GROUP_SLEEP = float(os.getenv("CRAWLER_INTER_GROUP_SLEEP", "3.0"))
# 每维关键词数量上限；与 InputParser 竞品词条数上限对齐，用于分组采集、
# tuple 分组和逐词缓存。
_MAX_KWS_PER_DIM = int(os.getenv("CRAWLER_MAX_KEYWORDS_PER_DIM", "5"))

# 详情补充：搜索列表 API 返回的 liked_count 常是 `100+` 这类模糊下界，
# 若要精确互动数据，需要逐条走详情接口 `/api/sns/web/v1/feed` 覆盖。
# 注意：viral_collector 路径本身通常已逐条补详情，所以这里的 enrich 是兜底，
# 用于详情失败后退回 brief 列表值的场景。
_ENRICH_ENABLED = os.getenv("CRAWLER_ENRICH_DETAILS", "true").strip().lower() in (
    "true",
    "1",
    "yes",
)
_ENRICH_TOP_N = int(os.getenv("CRAWLER_ENRICH_TOP_N", "30"))
_ENRICH_INTERVAL_SEC = float(os.getenv("CRAWLER_ENRICH_INTERVAL_SEC", "0.4"))
_ENRICH_PER_NOTE_TIMEOUT = int(os.getenv("CRAWLER_ENRICH_TIMEOUT", "12"))
_ENRICH_MAX_CONCURRENCY = int(os.getenv("CRAWLER_ENRICH_CONCURRENCY", "3"))

# ============================================================
# 阶段 4.x 三层缓存配置
# ============================================================
# 总开关：禁用时整条 L1/L2 路径都跳过，退化为纯 L3 实时爬取。
_CACHE_ENABLED = os.getenv("CRAWLER_CACHE_ENABLED", "true").strip().lower() in (
    "true",
    "1",
    "yes",
)
# L2 命中阈值：向量检索 top-K 中，source_keywords 与查询词表有交集的条数 >= _L2_MIN_HIT。
# 主词侧（行业维度词表）整组走 L1->L2；竞品侧按“每个竞品词”逐词 L1->L2 后合并。
# 只对未命中的词补爬，避免多词合成一把 key 时“库里有但仍全量爬”的情况。
# 两侧所需数据都齐时才整包缓存返回，否则只补缺失维度 / 缺失竞品词。
_L2_MIN_HIT = int(os.getenv("CRAWLER_L2_MIN_HIT", "10"))
# L2 查询 top-K（从 pgvector 一次拉多少条候选）
_L2_TOP_K = int(os.getenv("CRAWLER_L2_TOP_K", "30"))
# L2 有效时间窗（天）：超过该窗口的历史笔记不参与命中判定。
# 预热/回填缓存不一定每天运行，3 天窗口容易让已有缓存被误判为 miss；
# 默认放宽到 30 天，仍可通过 CRAWLER_L2_RECENT_DAYS 覆盖。
_L2_RECENT_DAYS = int(os.getenv("CRAWLER_L2_RECENT_DAYS", "30"))


def _coerce_str_list_field(raw: Any) -> List[Any]:
    """Normalize scalar/list input into a list for dimensions.* fields."""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, list):
        return raw
    s = str(raw).strip()
    return [s] if s else []


def _expand_keyword_tokens(raw_items: List[Any], *, max_terms: int) -> List[str]:
    """Split comma/space-separated keyword items into distinct terms."""
    out: List[str] = []
    seen: set[str] = set()
    for item in raw_items or []:
        s = str(item).strip()
        if not s:
            continue
        parts = re.split(r"[,，、;；\s]+", s)
        for p in parts:
            t = p.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
                if len(out) >= max_terms:
                    return out
    return out


def _dedupe_preserve_order_str(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _row_source_keywords_list(row: Dict[str, Any]) -> List[str]:
    sk = row.get("source_keywords") or []
    if not isinstance(sk, list):
        return []
    return [str(x).strip() for x in sk if str(x).strip()]


def _side_keyword_key_list(dims_keywords: Dict[str, List[str]], dim: str) -> List[str]:
    """返回某维度拟搜索词的排序去重列表，用作该侧 L1/L2 的 key 和覆盖校验。"""
    raw = [str(x).strip() for x in (dims_keywords.get(dim) or []) if str(x).strip()]
    return sorted(set(raw))


def _ordered_distinct_keywords(
    dims_keywords: Dict[str, List[str]], dim: str, *, max_n: Optional[int] = None
) -> List[str]:
    """按输入顺序去重，每维最多保留 `max_n` 个词，用于逐词缓存与补爬顺序。"""
    cap = max_n if max_n is not None else _MAX_KWS_PER_DIM
    seen: set[str] = set()
    out: List[str] = []
    for x in (dims_keywords.get(dim) or [])[:cap]:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _collapse_cache_layers(layers: List[str]) -> str:
    if not layers:
        return "L2"
    uniq = list(dict.fromkeys(layers))
    if len(uniq) == 1:
        return uniq[0]
    return "+".join(uniq)


def _kw_group_tuple(dims_keywords: Dict[str, List[str]], dim: str) -> Tuple[str, ...]:
    kws = dims_keywords.get(dim) or []
    return tuple(
        sorted(str(x).strip() for x in kws[:_MAX_KWS_PER_DIM] if str(x).strip())
    )


def _dims_sharing_kw_group(
    dims_keywords: Dict[str, List[str]],
    active_dims: List[str],
    ref_dim: str,
) -> List[str]:
    """返回与 `ref_dim` 关键词组相同的 active 维度列表。"""
    ref_t = _kw_group_tuple(dims_keywords, ref_dim)
    return [d for d in active_dims if _kw_group_tuple(dims_keywords, d) == ref_t]


def _copy_notes_for_dimension(
    notes: List[Dict[str, Any]],
    dim: str,
    active_dims: List[str],
) -> List[Dict[str, Any]]:
    return [
        {
            **n,
            "dimension": dim,
            "dimensions_hit": list(
                dict.fromkeys((n.get("dimensions_hit") or []) + [dim])
            ),
        }
        for n in notes
    ]


def _filter_notes_for_dimension(
    notes: List[Dict[str, Any]],
    dims_keywords: Dict[str, List[str]],
    dim: str,
) -> List[Dict[str, Any]]:
    """按维度关键词和已有维度标签过滤笔记，避免 merged cache pool 污染各维样本。

    - 维度未配置关键词时：不做关键词过滤（返回去重后的原列表），避免误杀整池。
    - 过滤结果为空但输入非空时：回退为原池（常见于缓存行缺少 source_keywords 字段）。
    """
    required = {
        str(kw).strip()
        for kw in (dims_keywords.get(dim) or [])
        if str(kw).strip()
    }
    if not required:
        return _dedupe_notes_by_note_id(list(notes))

    out: List[Dict[str, Any]] = []
    for note in notes:
        sk = set(_row_source_keywords_list(note))
        if sk & required:
            out.append(note)
            continue

        note_dim = str(note.get("dimension") or "").strip()
        hits = {str(x).strip() for x in (note.get("dimensions_hit") or []) if str(x).strip()}
        if note_dim == dim or dim in hits:
            out.append(note)
    deduped = _dedupe_notes_by_note_id(out)
    if deduped:
        return deduped
    if notes:
        return _dedupe_notes_by_note_id(list(notes))
    return []


def _notes_cover_all_required_sk_keywords(
    notes: List[Dict[str, Any]], required_kws: List[str]
) -> bool:
    """判断 required_kws 中每个词是否至少在一条笔记的 source_keywords 中出现。"""
    if not required_kws:
        return True
    for kw in required_kws:
        if not any(kw in _row_source_keywords_list(n) for n in notes):
            return False
    return True


def _dedupe_notes_by_note_id(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for n in notes:
        nid = str(n.get("note_id") or "").strip()
        key = nid or f"__noid_{(n.get('title') or '')[:24]}"
        by_key.setdefault(key, n)
    return list(by_key.values())


def _cache_trace_payload(
    *,
    cache_source: str,
    cache_keywords: List[str],
    main_key: List[str],
    comp_key: List[str],
    main_layer: Optional[str],
    comp_layer: Optional[str],
    comp_missed_keywords: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """构建主词侧和竞品侧的缓存命中说明。"""
    if cache_source == "L3":
        summary = "存在侧缓存未命中，已对缺样本的维度执行实时采集。"
    elif cache_source == "stub":
        summary = "当前为 stub 数据，或未完整走完缓存链路。"
    else:
        summary = f"主词侧与竞品侧均命中缓存（{cache_source}），合并后直接返回。"
    out = {
        "cache_source": cache_source,
        "cache_keywords": list(cache_keywords),
        "main_keywords": list(main_key),
        "competitor_keywords": list(comp_key),
        "main_cache_layer": main_layer,
        "competitor_cache_layer": comp_layer,
        "summary": summary,
    }
    if comp_missed_keywords:
        out["competitor_cache_miss_keywords"] = list(comp_missed_keywords)
    return out


def _single_kw_same_pool(main_key: List[str], comp_key: List[str]) -> bool:
    """主词侧与竞品侧是否退化为同一个单关键词池，仅此场景允许共用一次 L1/L2 结果。"""
    return bool(main_key) and main_key == comp_key and len(main_key) == 1


def _merge_side_cache_notes(
    main_res: Optional[Tuple[List[Dict[str, Any]], str]],
    comp_res: Optional[Tuple[List[Dict[str, Any]], str]],
) -> List[Dict[str, Any]]:
    parts: List[List[Dict[str, Any]]] = []
    if main_res:
        parts.append(main_res[0])
    if comp_res and comp_res is not main_res:
        parts.append(comp_res[0])
    elif not main_res and comp_res:
        parts.append(comp_res[0])
    return _dedupe_notes_by_note_id([n for p in parts for n in p])


def _per_dim_sources_for_dual_cache(
    dims_keywords: Dict[str, List[str]],
    active_dims: List[str],
    main_res: Optional[Tuple[List[Dict[str, Any]], str]],
    comp_res: Optional[Tuple[List[Dict[str, Any]], str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """为 active 维度构造对应的缓存笔记池，供 samples_by_dimension 分维复制。

    注意：当行业/竞品关键词组 tuple 相同（ti == tc）时，必须按维度语义优先拆分，
    否则 competitor 会错误拿到 main_res，导致 Sheet4 和 Sheet3 混成同一批笔记。
    """
    ti = _kw_group_tuple(dims_keywords, "industry")
    tc = _kw_group_tuple(dims_keywords, "competitor")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for d in active_dims:
        t = _kw_group_tuple(dims_keywords, d)
        if d == "competitor":
            if comp_res:
                out[d] = comp_res[0]
            elif main_res:
                out[d] = main_res[0]
            continue
        if d == "industry":
            if main_res:
                out[d] = main_res[0]
            elif comp_res:
                out[d] = comp_res[0]
            continue
        if d == "brand":
            if t == tc and comp_res:
                out[d] = comp_res[0]
            elif t == ti and main_res:
                out[d] = main_res[0]
            elif main_res:
                out[d] = main_res[0]
            elif comp_res:
                out[d] = comp_res[0]
            continue
        if t == ti and main_res:
            out[d] = main_res[0]
        elif t == tc and comp_res:
            out[d] = comp_res[0]
        elif t == ti == tc and main_res:
            out[d] = main_res[0]
        elif t == ti == tc and comp_res:
            out[d] = comp_res[0]
    return out


def _can_serve_only_from_side_caches(
    active_dims: List[str],
    dims_keywords: Dict[str, List[str]],
    main_res: Optional[Tuple[List[Dict[str, Any]], str]],
    comp_res: Optional[Tuple[List[Dict[str, Any]], str]],
    main_missed_keywords: Optional[List[str]] = None,
    comp_missed_keywords: Optional[List[str]] = None,
) -> bool:
    """判断 active 维度是否都能仅靠主词侧 / 竞品侧缓存满足。"""
    main_miss = list(main_missed_keywords or [])
    comp_miss = list(comp_missed_keywords or [])
    ti = _kw_group_tuple(dims_keywords, "industry")
    tc = _kw_group_tuple(dims_keywords, "competitor")
    needs_main = bool(ti) and any(
        _kw_group_tuple(dims_keywords, d) == ti for d in active_dims
    )
    needs_comp = bool(tc) and tc != ti and any(
        _kw_group_tuple(dims_keywords, d) == tc for d in active_dims
    )
    if needs_main and (main_res is None or main_miss):
        return False
    if needs_comp and (comp_res is None or comp_miss):
        return False
    if not needs_main and not needs_comp:
        return False
    for d in active_dims:
        t = _kw_group_tuple(dims_keywords, d)
        if not t:
            continue
        if t != ti and t != tc:
            return False
    return True


def _dual_cache_source_label(
    main_res: Optional[Tuple[List[Dict[str, Any]], str]],
    comp_res: Optional[Tuple[List[Dict[str, Any]], str]],
    main_key: List[str],
    comp_key: List[str],
) -> str:
    if _single_kw_same_pool(main_key, comp_key) and main_res:
        return main_res[1]
    ml = main_res[1] if main_res else ""
    cl = ""
    if comp_res and comp_res is not main_res:
        cl = comp_res[1]
    elif not main_res and comp_res:
        cl = comp_res[1]
    if ml and cl:
        return ml if ml == cl else f"{ml}+{cl}"
    return ml or cl or "L2"


def _dual_trace_layers(
    main_res: Optional[Tuple[List[Dict[str, Any]], str]],
    comp_res: Optional[Tuple[List[Dict[str, Any]], str]],
    main_key: List[str],
    comp_key: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    ml = main_res[1] if main_res else None
    if _single_kw_same_pool(main_key, comp_key) and main_res:
        return ml, ml
    cl = comp_res[1] if comp_res else None
    return ml, cl


class CrawlerAgent(BaseAgent):
    agent_id = "CrawlerAgent"
    # 4.3pre.3: 采集层喂给总览 + 两源画布样本(品类 TOP 仅统计/矩阵用,无独立画布卡)
    #  - mod-overview-stats:          Sheet 1（统计总览）
    #  - mod-competitor-samples:      竞品爆文
    #  - mod-top-interaction-samples: 互动 TOP
    provides = [
        "mod-overview-stats",
        "mod-competitor-samples",
        "mod-top-interaction-samples",
    ]
    depends_on: List[str] = []
    write_partition = "crawler_output"

    async def run(self, context: AgentContext) -> AgentResult:
        task_id = context.task_id
        input_spec = context.task_context.get("input_spec") or {}
        parsed = input_spec.get("parsed") or {}
        base_keywords: List[str] = list(
            parsed.get("keywords") or input_spec.get("keywords") or []
        )
        dims_input: Dict[str, Any] = dict(parsed.get("dimensions") or {})

        dims_keywords: Dict[str, List[str]] = {
            "industry": (
                _expand_keyword_tokens(
                    _coerce_str_list_field(dims_input.get("industry")),
                    max_terms=_MAX_KWS_PER_DIM,
                )
                or list(base_keywords)
            ),
            "competitor": _expand_keyword_tokens(
                _coerce_str_list_field(dims_input.get("competitor")),
                max_terms=_MAX_KWS_PER_DIM,
            ),
            "brand": _expand_keyword_tokens(
                _coerce_str_list_field(dims_input.get("brand")),
                max_terms=_MAX_KWS_PER_DIM,
            ),
        }

        # ---------- 竞品维度：根据来源决定是否只使用显式竞品词 ----------
        competitor_source = str(parsed.get("competitor_source") or "llm_inferred").strip()
        # 也直接检查 API 字段 competitor_keywords（兼容 InputParserAgent 未运行的场景）
        api_comp = _expand_keyword_tokens(
            _coerce_str_list_field(input_spec.get("competitor_keywords")),
            max_terms=_MAX_KWS_PER_DIM,
        )
        if api_comp:
            # API 字段显式给出竞品词时，只使用这些词，忽略 parsed 中可能的 LLM 推断词
            dims_keywords["competitor"] = list(api_comp)[:_MAX_KWS_PER_DIM]
        elif competitor_source in ("api", "user_explicit"):
            # 用户在自然语言里明确提到竞品时，只使用 parsed 里的显式词
            comp_from_parsed = _coerce_str_list_field(dims_input.get("competitor"))
            comp_expanded = _expand_keyword_tokens(comp_from_parsed, max_terms=_MAX_KWS_PER_DIM)
            if comp_expanded:
                dims_keywords["competitor"] = comp_expanded[:_MAX_KWS_PER_DIM]

        active_dims = [d for d in _DIMENSIONS if dims_keywords[d]]

        # 若用户明确声明不需要竞品，从 active_dims 中移除 competitor 维度
        pipeline_cfg = (parsed.get("pipeline_config") or {})
        if pipeline_cfg.get("skip_competitor") or str(parsed.get("competitor_source") or "") == "user_skip":
            active_dims = [d for d in active_dims if d != "competitor"]
            dims_keywords["competitor"] = []
            logger.info("[Crawler] skip_competitor=True，已跳过竞品维度爬取")

        # 解析前端高级配置，用户 UI 调整优先于 env 默认值
        advanced_config = input_spec.get("advanced_config") or {}
        runtime_cfg = _parse_advanced_config(advanced_config)

        # 聚合关键词（用于观测 / 回写 L2 等）；主词 / 竞品均按逐词 L1->L2，避免整组 key 与逐词回写不一致
        cache_keywords = _collect_cache_keywords(dims_keywords, active_dims)
        main_key = _side_keyword_key_list(dims_keywords, "industry")
        main_ordered = _ordered_distinct_keywords(dims_keywords, "industry")
        comp_ordered = _ordered_distinct_keywords(dims_keywords, "competitor")
        comp_key = sorted(set(comp_ordered))
        brand_ordered = _ordered_distinct_keywords(dims_keywords, "brand")
        main_missed: List[str] = []
        comp_missed: List[str] = []
        brand_missed: List[str] = []

        await self.emit_progress(
            task_id,
            f"开始采集：{len(active_dims)} 个维度 · 目标 {runtime_cfg['target_count']} 条",
            progress=10,
        )
        # 主动让出事件循环，给上游 cancel 机会命中（stub 降级路径下尤其重要）
        await asyncio.sleep(0)

        # ============================================================
        # 阶段 4.x 三层缓存：主词 / 竞品均逐词 L1->L2；双侧齐全时整包返回
        # ============================================================
        main_res: Optional[Tuple[List[Dict[str, Any]], str]] = None
        comp_res: Optional[Tuple[List[Dict[str, Any]], str]] = None
        brand_res: Optional[Tuple[List[Dict[str, Any]], str]] = None

        if _CACHE_ENABLED:
            if main_ordered:
                main_res, main_missed = await self._resolve_per_word_keyword_caches(
                    task_id, main_ordered
                )
            if comp_ordered:
                comp_res, comp_missed = await self._resolve_per_word_keyword_caches(
                    task_id, comp_ordered
                )
            if brand_ordered:
                brand_res, brand_missed = await self._resolve_per_word_keyword_caches(
                    task_id, brand_ordered
                )

        if _CACHE_ENABLED and main_ordered:
            await self.emit_log(
                task_id,
                "info",
                f"主词（行业）逐词缓存: {len(main_ordered) - len(main_missed)}/{len(main_ordered)} 词命中 · "
                f"未命中需补爬={main_missed or '无'}",
            )
        if _CACHE_ENABLED and comp_ordered:
            await self.emit_log(
                task_id,
                "info",
                f"竞品词逐词缓存: {len(comp_ordered) - len(comp_missed)}/{len(comp_ordered)} 词命中 · "
                f"未命中需补爬={comp_missed or '无'}",
            )

        if _CACHE_ENABLED and brand_ordered:
            await self.emit_log(
                task_id,
                "info",
                f"本品词逐词缓存: {len(brand_ordered) - len(brand_missed)}/{len(brand_ordered)} 词命中 · "
                f"未命中需补爬={brand_missed or '无'}",
            )

        cache_res_by_dim: Dict[str, Optional[Tuple[List[Dict[str, Any]], str]]] = {
            "industry": main_res,
            "competitor": comp_res,
            "brand": brand_res,
        }
        cache_missed_by_dim: Dict[str, List[str]] = {
            "industry": list(main_missed),
            "competitor": list(comp_missed),
            "brand": list(brand_missed),
        }

        if _CACHE_ENABLED and active_dims:
            cache_only_ready = True
            per_dim_cache: Dict[str, List[Dict[str, Any]]] = {}
            merged_cache_parts: List[Dict[str, Any]] = []
            cache_layers: List[str] = []
            for dim in active_dims:
                ordered = _ordered_distinct_keywords(dims_keywords, dim)
                if not ordered:
                    continue
                res = cache_res_by_dim.get(dim)
                missed = cache_missed_by_dim.get(dim) or []
                if res is None or missed:
                    cache_only_ready = False
                    break
                filtered = _filter_notes_for_dimension(res[0], dims_keywords, dim)
                if not filtered:
                    cache_only_ready = False
                    break
                per_dim_cache[dim] = filtered
                merged_cache_parts.extend(filtered)
                cache_layers.append(res[1])

            if cache_only_ready and per_dim_cache:
                return await self._build_result_from_cache(
                    context,
                    _dedupe_notes_by_note_id(merged_cache_parts),
                    dims_keywords,
                    active_dims,
                    runtime_cfg,
                    cache_source=_collapse_cache_layers(cache_layers),
                    cache_keywords=cache_keywords,
                    main_key=main_key,
                    comp_key=comp_key,
                    main_layer=main_res[1] if main_res else None,
                    comp_layer=comp_res[1] if comp_res else None,
                    per_dim_sources=per_dim_cache,
                    comp_missed_keywords=None,
                )

        if any(cache_res_by_dim.values()):
            await self.emit_log(
                task_id, "info", "缓存部分命中，对仍缺样本的维度走 L3 补采"
            )
        else:
            await self.emit_log(task_id, "info", "缓存未命中，走 L3 实时采集")

        # 获取 cookies
        owner_user_id = _resolve_owner_user_id(task_id)
        cookies_str = _resolve_cookies_str(owner_user_id)

        samples_by_dim: Dict[str, List[Dict[str, Any]]] = {d: [] for d in _DIMENSIONS}
        dimension_status: Dict[str, str] = {d: "empty" for d in _DIMENSIONS}

        for dim, res in cache_res_by_dim.items():
            if dim not in active_dims or res is None:
                continue
            filtered = _filter_notes_for_dimension(res[0], dims_keywords, dim)
            if not filtered:
                continue
            samples_by_dim[dim] = _copy_notes_for_dimension(
                filtered, dim, active_dims
            )
            dimension_status[dim] = res[1].lower()

        dims_limit: Set[str] = {d for d in active_dims if not samples_by_dim[d]}
        for dim in active_dims:
            if cache_missed_by_dim.get(dim):
                dims_limit.add(dim)

        prior_cached_samples: Dict[str, List[Dict[str, Any]]] = {
            dim: list(samples_by_dim.get(dim) or [])
            for dim in active_dims
            if cache_missed_by_dim.get(dim)
        }

        crawl_kw_override = None
        if any(cache_missed_by_dim.get(dim) for dim in active_dims):
            crawl_kw_override = {k: list(v) for k, v in dims_keywords.items()}
            for dim in list(active_dims):
                missed = cache_missed_by_dim.get(dim) or []
                if missed:
                    crawl_kw_override[dim] = list(missed)
                elif cache_res_by_dim.get(dim) is not None:
                    dims_limit.discard(dim)
                    crawl_kw_override.pop(dim, None)

        if cookies_str and dims_limit:
            await self._run_real_collection(
                task_id,
                cookies_str,
                dims_keywords,
                active_dims,
                samples_by_dim,
                dimension_status,
                runtime_cfg,
                dims_limit=dims_limit,
                keywords_by_dim_override=crawl_kw_override,
            )

        for dim, prior in prior_cached_samples.items():
            if not prior:
                continue
            post = samples_by_dim.get(dim) or []
            combined = _dedupe_notes_by_note_id(prior + post)
            samples_by_dim[dim] = _copy_notes_for_dimension(
                combined, dim, active_dims
            )

        if not cookies_str:
            await self.emit_log(
                task_id, "warn", "CrawlerAgent 未找到可用 cookies，回退到 stub 样本"
            )

        # 合并 + 去重（按 note_id）降级
        # hotfix 2：之前直接 extend 所有维度样本，会让共享一次采集的结果被重复复制，
        # 最终从 3 条膨胀成 9 条，导致画布原始数据模块显示重复。这里按 note_id 去重，
        # 同一篇笔记只保留一条，同时记录它命中了哪些维度到 dimensions_hit。
        by_id: Dict[str, Dict[str, Any]] = {}
        for dim in _DIMENSIONS:
            for note in samples_by_dim[dim]:
                note_id = note.get("note_id", "")
                key = note_id or f"__noid_{note.get('title', '')[:24]}_{note.get('keyword', '')}"
                if key in by_id:
                    existing = by_id[key]
                    hits = existing.setdefault("dimensions_hit", [])
                    cur_dim = note.get("dimension")
                    if cur_dim and cur_dim not in hits:
                        hits.append(cur_dim)
                else:
                    merged = dict(note)
                    cur_dim = note.get("dimension")
                    merged["dimensions_hit"] = [cur_dim] if cur_dim else []
                    by_id[key] = merged

        all_notes: List[Dict[str, Any]] = list(by_id.values())

        source = "live"
        if not all_notes:
            await self.emit_log(
                task_id, "warn", "三维采集均未拿到笔记，使用 stub 占位数据"
            )
            samples_by_dim, all_notes = _build_stub(dims_keywords, active_dims)
            for dim in active_dims:
                dimension_status[dim] = "stub"
            source = "stub"

        # 详情补充：搜索列表 API 的 liked_count 常是模糊下界（如 `100+`），
        # 这里调用详情接口拿精确值并回写到每条笔记，同时同步给 samples_by_dim，
        # 保证下游 InsightAgent 按维度读取到的也是精确数据。
        if source == "live" and _ENRICH_ENABLED and cookies_str:
            await self._enrich_with_details(task_id, cookies_str, all_notes)
            _propagate_enriched_to_dim_samples(all_notes, samples_by_dim)

        notes_image = [n for n in all_notes if n.get("media_type") == "image"]
        notes_video = [n for n in all_notes if n.get("media_type") == "video"]

        # L3 实时路径成功后回写缓存，下次同词直接命中（主词 / 竞品 / 本品逐词 key）
        cache_source = "L3" if source == "live" else "stub"
        if source == "live" and all_notes and _CACHE_ENABLED:
            seen_kw: set[str] = set()
            for kw in list(main_ordered) + list(comp_ordered) + list(brand_ordered):
                if kw in seen_kw:
                    continue
                seen_kw.add(kw)
                await self._write_back_l1([kw], all_notes)
            await self._write_back_l2(all_notes)

        # 构造三源视图，供下游 Agent（ViralModel / Insight）消费
        sources, all_notes_with_hits = _build_four_source_view(
            all_notes, samples_by_dim,
        )
        for n in all_notes_with_hits:
            _backfill_seo_top10_on_dict(n)

        if source == "live" and cookies_str:
            await self._enrich_competitor_comment_hotwords(
                task_id, sources, cookies_str
            )

        # 互动量下限过滤
        _min_inter = int(runtime_cfg.get("min_interaction") or 0)
        if _min_inter:
            _before = len(all_notes_with_hits)
            all_notes_with_hits = _apply_min_interaction_filter(all_notes_with_hits, _min_inter)
            notes_image = _apply_min_interaction_filter(notes_image, _min_inter)
            notes_video = _apply_min_interaction_filter(notes_video, _min_inter)
            await self.emit_log(
                task_id, "info",
                f"互动量过滤（≥{_min_inter}）：{_before} → {len(all_notes_with_hits)} 条",
            )

        _ml_tr, _cl_tr = _dual_trace_layers(main_res, comp_res, main_key, comp_key)
        sample = {
            "source": source,
            "cache_source": cache_source,
            "sample_count": len(all_notes_with_hits),
            "keywords": base_keywords or [input_spec.get("raw_input", "")],
            "dimensions_keywords": dims_keywords,
            "samples_by_dimension": samples_by_dim,
            "notes_image": notes_image,
            "notes_video": notes_video,
            "note_summary": all_notes_with_hits,
            "dimension_status": dimension_status,
            # 4.3pre.2 新增：四源视图 + 合并视图
            "sources": sources,
            "all_notes": all_notes_with_hits,
            "competitor_cache_trace": _cache_trace_payload(
                cache_source=cache_source,
                cache_keywords=cache_keywords,
                main_key=main_key,
                comp_key=comp_key,
                main_layer=_ml_tr,
                comp_layer=_cl_tr,
                comp_missed_keywords=comp_missed or None,
            ),
        }

        TaskContextWriter(context.task_context).write(
            "crawler_output", sample, agent_id=self.agent_id, note="3d_real_collection"
        )

        await self.emit_progress(
            task_id,
            f"采集完成（{cache_source}）：共 {len(all_notes)} 条，图文 {len(notes_image)} / 视频 {len(notes_video)}",
            progress=30,
        )
        return AgentResult(ok=True, produced_modules=self.provides, output=sample)

    async def _react_keyword_retry(self, task_id: str, keywords: List[str]) -> List[str]:
        """[ReAct] 关键词采集结果为 0 时，调 LLM 生成 1-2 个同义词重试一次。

        仅在 L1/L2 缓存未命中且真实采集结果为 0 时触发；每组最多重试 1 次。
        """
        try:
            kw_str = "、".join(keywords[:3])
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个小红书关键词助手。"
                        "请为以下关键词生成 1-2 个在小红书上更容易搜到内容的同义词或相关词，"
                        "直接输出词，多个词用逗号分隔，不要解释。"
                    ),
                },
                {"role": "user", "content": f"关键词：{kw_str}"},
            ]
            response = await asyncio.wait_for(
                self._gateway.chat(
                    "CrawlerAgent",
                    messages,
                    modality="text",
                    task_id=task_id,
                    overrides={"temperature": 0.3, "max_tokens": 60},
                ),
                timeout=15,
            )
            text = str(response.get("content") or "").strip()
            if not text:
                return []
            synonyms = [s.strip() for s in re.split(r"[,，、\s]+", text) if s.strip()]
            orig_set = {k.lower() for k in keywords}
            filtered = [s for s in synonyms if s.lower() not in orig_set][:2]
            if filtered:
                await self.emit_log(
                    task_id, "info",
                    f"[ReAct] LLM 生成同义词: {filtered}（原词: {kw_str}）",
                )
            return filtered
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ReAct] 关键词扩展失败，跳过重试: {}", exc)
            return []

    async def _run_real_collection(
        self,
        task_id: str,
        cookies_str: str,
        dims_keywords: Dict[str, List[str]],
        active_dims: List[str],
        samples_by_dim: Dict[str, List[Dict[str, Any]]],
        dimension_status: Dict[str, str],
        runtime_cfg: Dict[str, Any],
        dims_limit: Optional[Set[str]] = None,
        keywords_by_dim_override: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """按 keywords 去重并对不同组串行采集，规避 XHS 软反爬。

        设计要点：
        1. 多个维度 keywords 完全相同时，只真实采集一次，结果复制给同组维度。
        2. 不同 keyword 组串行而非并行，避免同 IP + 同 cookies 并发触发 XHS 风控。
        3. 每组独立超时，单组失败不影响其它组。
        4. 采集参数全部来自 runtime_cfg（前端高级配置），不再读取硬编码 env。
        """
        user_analysis_target = max(5, int(runtime_cfg["target_count"]))
        per_group_target = user_analysis_target
        eff_kw: Dict[str, List[str]] = (
            keywords_by_dim_override
            if keywords_by_dim_override is not None
            else dims_keywords
        )

        # 按 keywords tuple 分组
        keyword_groups: Dict[Tuple[str, ...], List[str]] = {}
        for dim in active_dims:
            if dims_limit is not None and dim not in dims_limit:
                continue
            kws = eff_kw[dim][:_MAX_KWS_PER_DIM]
            if not kws:
                continue
            # 使用 sorted tuple 作为 key，保证 "A,B" 和 "B,A" 视为同组
            key = tuple(sorted(kws))
            keyword_groups.setdefault(key, []).append(dim)

        if not keyword_groups:
            return

        # 打印分组情况
        group_desc = [
            f"{'+'.join(dims)}={list(kws)}" for kws, dims in keyword_groups.items()
        ]
        await self.emit_log(
            task_id,
            "info",
            f"关键词分组（共 {len(keyword_groups)} 组）：{' | '.join(group_desc)}"
            f" · 采集目标 {per_group_target} 条/组（分析目标 {user_analysis_target}）",
        )

        # 逐组串行采集
        for group_idx, (kw_tuple, dim_list) in enumerate(keyword_groups.items(), start=1):
            kws = list(kw_tuple)
            try:
                notes = await asyncio.wait_for(
                    _collect_one_dimension(cookies_str, kws, per_group_target, runtime_cfg),
                    timeout=_PER_GROUP_TIMEOUT,
                )
                primary_dim = dim_list[0]
                if notes:
                    await self.emit_log(
                        task_id,
                        "info",
                        f"[{group_idx}/{len(keyword_groups)}] {dim_list} 采到 {len(notes)} 条 · 首条: {getattr(notes[0], 'title', '')[:40]}",
                    )
                else:
                    await self.emit_log(
                        task_id,
                        "warn",
                        f"[{group_idx}/{len(keyword_groups)}] {dim_list} 采到 0 条 (keywords={kws})。"
                        "可能原因：小红书软反爬、cookie 降权、关键词过冷门，或网络异常。"
                        "建议：先在浏览器手动验证关键词，或重新扫码刷新 cookie。",
                    )
                    # [ReAct] 关键词 0 结果时自动尝试同义词重试一次
                    retry_kws = await self._react_keyword_retry(task_id, kws)
                    if retry_kws:
                        try:
                            retry_notes = await asyncio.wait_for(
                                _collect_one_dimension(cookies_str, retry_kws, per_group_target, runtime_cfg),
                                timeout=_PER_GROUP_TIMEOUT,
                            )
                            if retry_notes:
                                notes = retry_notes
                                kws = retry_kws
                                await self.emit_log(
                                    task_id, "info",
                                    f"[ReAct] 同义词重试成功: {retry_kws} → {len(notes)} 条",
                                )
                            else:
                                await self.emit_log(
                                    task_id, "warn",
                                    f"[ReAct] 同义词重试仍为 0 条: {retry_kws}，放弃重试",
                                )
                        except Exception as _retry_exc:  # noqa: BLE001
                            await self.emit_log(task_id, "debug", f"[ReAct] 重试采集异常: {_retry_exc}")
                # 同一批 notes 分配给共享这组 keywords 的所有维度。
                for dim in dim_list:
                    normalized = [_normalize_note(n, dim, kws[0]) for n in notes]
                    samples_by_dim[dim] = normalized
                    if dim == primary_dim:
                        dimension_status[dim] = "ok" if normalized else "empty"
                    else:
                        dimension_status[dim] = (
                            f"shared:{primary_dim}" if normalized else "empty"
                        )
            except asyncio.TimeoutError:
                for dim in dim_list:
                    dimension_status[dim] = "timeout"
                await self.emit_log(
                    task_id,
                    "warn",
                    f"[{group_idx}/{len(keyword_groups)}] {dim_list} 采集超时 ({_PER_GROUP_TIMEOUT}s)",
                )
            except Exception as exc:  # noqa: BLE001
                for dim in dim_list:
                    dimension_status[dim] = "failed"
                await self.emit_log(
                    task_id,
                    "warn",
                    f"[{group_idx}/{len(keyword_groups)}] {dim_list} 采集失败：{type(exc).__name__}: {exc}",
                )

            # 组间间隔，避免连续命中 XHS 风控；最后一组不等待。
            if group_idx < len(keyword_groups) and _INTER_GROUP_SLEEP > 0:
                await asyncio.sleep(_INTER_GROUP_SLEEP)

    # ==================================================================
    # 阶段 4.x 三层缓存私有方法
    # ==================================================================

    async def _try_l1_cache(
        self, task_id: str, side_key: List[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """查询 L1 Redis；key 为单侧词表，命中笔记需覆盖该侧每个词在 source_keywords 中的出现。"""
        if not side_key:
            return None
        try:
            from ...infrastructure.cache.keyword_cache import get_keyword_cache

            cache = get_keyword_cache()
            cached = await cache.get(side_key)
            if cached:
                if not _notes_cover_all_required_sk_keywords(cached, side_key):
                    await self.emit_log(
                        task_id,
                        "info",
                        "L1 Redis key 命中，但该侧关键词在笔记 source_keywords 上未全覆盖，"
                        f"视为 miss（side_key={side_key}）。",
                    )
                    return None
                await self.emit_log(
                    task_id,
                    "info",
                    f"命中 L1 Redis 缓存: {len(cached)} 条 (side_key={side_key})",
                )
                return cached
        except Exception as exc:  # noqa: BLE001
            await self.emit_log(task_id, "debug", f"L1 查询异常，降级 miss: {exc}")
        return None

    async def _try_l2_cache(
        self, task_id: str, side_key: List[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """L2：该侧词 OR 检索 + 每词 SK 覆盖 + side_key 交集命中阈值。"""
        if not side_key:
            return None
        try:
            from ...infrastructure.storage.notes_vector_store import (
                get_notes_vector_store,
            )

            store = get_notes_vector_store()
            rows = _dedupe_notes_by_note_id(
                await store.search_notes(
                    keywords=side_key,
                    top_k=_L2_TOP_K,
                    recent_days=_L2_RECENT_DAYS,
                )
            )
            for kw in side_key:
                if not any(kw in _row_source_keywords_list(r) for r in rows):
                    extra = await store.search_notes(
                        keywords=[kw],
                        top_k=_L2_TOP_K,
                        recent_days=_L2_RECENT_DAYS,
                    )
                    rows = _dedupe_notes_by_note_id(rows + list(extra))

            if not _notes_cover_all_required_sk_keywords(rows, side_key):
                if rows:
                    await self.emit_log(
                        task_id,
                        "debug",
                        f"L2 该侧词 source_keywords 未全覆盖 (side_key={side_key})，走 L3",
                    )
                return None

            sk_lists = [_row_source_keywords_list(r) for r in rows]
            hit_count = sum(1 for sk in sk_lists if any(kw in sk for kw in side_key))
            if hit_count >= _L2_MIN_HIT:
                await self.emit_log(
                    task_id,
                    "info",
                    f"命中 L2 pgvector: 该侧同词 {hit_count} 条 / 候选 {len(rows)} 条 · "
                    f"side_key={side_key}",
                )
                return rows
            if hit_count > 0:
                await self.emit_log(
                    task_id,
                    "debug",
                    f"L2 部分命中但未达阈值 ({hit_count}/{_L2_MIN_HIT})，走 L3 实时采集",
                )
        except Exception as exc:  # noqa: BLE001
            await self.emit_log(task_id, "debug", f"L2 查询异常，降级 miss: {exc}")
        return None

    async def _resolve_side_cache(
        self, task_id: str, side_key: List[str]
    ) -> Optional[Tuple[List[Dict[str, Any]], str]]:
        """单侧执行 L1 -> L2，返回 (notes, layer) 或 None。"""
        if not side_key:
            return None
        l1 = await self._try_l1_cache(task_id, side_key)
        if l1 is not None:
            return (l1, "L1")
        l2 = await self._try_l2_cache(task_id, side_key)
        if l2 is not None:
            await self._write_back_l1(side_key, l2)
            return (l2, "L2")
        return None

    async def _resolve_per_word_keyword_caches(
        self,
        task_id: str,
        ordered_kws: List[str],
        *,
        reuse_single_kw: Optional[Tuple[str, Tuple[List[Dict[str, Any]], str]]] = None,
    ) -> Tuple[Optional[Tuple[List[Dict[str, Any]], str]], List[str]]:
        """按词逐个走 L1->L2，合并命中笔记；返回的 miss 列表只包含确实未命中的词。"""
        if not ordered_kws:
            return None, []
        if (
            len(ordered_kws) == 1
            and reuse_single_kw is not None
            and ordered_kws[0] == reuse_single_kw[0]
        ):
            return reuse_single_kw[1], []
        chunks: List[List[Dict[str, Any]]] = []
        layers: List[str] = []
        missed: List[str] = []
        for kw in ordered_kws:
            res = await self._resolve_side_cache(task_id, [kw])
            if res is None:
                missed.append(kw)
            else:
                chunks.append(res[0])
                layers.append(res[1])
        if not chunks:
            return None, missed
        merged = _dedupe_notes_by_note_id([n for c in chunks for n in c])
        label = _collapse_cache_layers(layers)
        return (merged, label), missed

    async def _write_back_l1(
        self, cache_keywords: List[str], notes: List[Dict[str, Any]]
    ) -> None:
        """L3 / L2 命中后顺手回填 L1，加速下一次查询。"""
        try:
            from ...infrastructure.cache.keyword_cache import get_keyword_cache

            await get_keyword_cache().set(cache_keywords, notes)
        except Exception:  # noqa: BLE001
            pass  # 回写失败不影响主流程

    async def _enrich_competitor_comment_hotwords(
        self,
        task_id: str,
        sources: Dict[str, List[Dict[str, Any]]],
        cookies_str: str,
    ) -> None:
        """为竞品源笔记拉取首屏评论并分词，回填到 comment_hotwords_top10。"""
        logger.info(
            "[comment_hotwords] enter task_id={} enabled={} cookie_len={} cookie_prefix={}",
            task_id,
            _CRAWLER_COMMENT_HW_ENABLED,
            len(cookies_str),
            cookies_str[:30] if cookies_str else "EMPTY",
        )
        if not _CRAWLER_COMMENT_HW_ENABLED or not cookies_str:
            logger.warning(
                "[comment_hotwords] skipped: enabled={} cookie_present={}",
                _CRAWLER_COMMENT_HW_ENABLED,
                bool(cookies_str),
            )
            return
        comp = list(sources.get("competitor") or [])
        if not comp:
            logger.warning("[comment_hotwords] skipped: no competitor notes in sources")
            return
        comp.sort(key=_interaction_sort_key, reverse=True)
        cap = max(0, _competitor_comment_max_notes_from_env())
        chosen = comp[:cap]
        if not chosen:
            logger.warning("[comment_hotwords] skipped: chosen empty after cap={}", cap)
            return
        if len(comp) > cap:
            await self.emit_log(
                task_id,
                "info",
                f"竞品评论热词：仅对互动 Top {cap} 条拉评论（竞品池共 {len(comp)} 条），"
                f"其余行 U 列留空；可调环境变量 CRAWLER_COMPETITOR_COMMENT_MAX_NOTES。",
            )

        # 逐条诊断 xsec_token 初始状态
        xsec_diag: List[str] = []
        for n in chosen:
            nid = str(n.get("note_id") or "")[:12]
            url = _note_url_candidates(n)[:60]
            has_xsec = bool(_resolve_xsec_for_comment_api(n))
            xsec_diag.append(f"{nid}(xsec={'Y' if has_xsec else 'N'})")
        logger.info(
            "[comment_hotwords] chosen={} cap={} xsec_diag: {}",
            len(chosen),
            cap,
            " | ".join(xsec_diag[:10]) + (" ..." if len(xsec_diag) > 10 else ""),
        )

        no_xsec_initial = sum(1 for n in chosen if not _resolve_xsec_for_comment_api(n))
        if no_xsec_initial:
            await self.emit_log(
                task_id,
                "info",
                f"竞品评论热词：本批 {len(chosen)} 条中 {no_xsec_initial} 条初始无 xsec_token，"
                f"将尝试用 feed 接口补 token 后再拉评论。",
            )

        sem = asyncio.Semaphore(3)

        async def _one(
            n: Dict[str, Any],
        ) -> tuple[str, List[str], Optional[str]]:
            async with sem:
                hw, err = await asyncio.to_thread(
                    _fetch_comment_hotwords_sync, n, cookies_str
                )
                return (str(n.get("note_id") or ""), hw, err)

        results = await asyncio.gather(*[_one(n) for n in chosen], return_exceptions=True)
        by_nid: Dict[str, List[str]] = {}
        first_err: Optional[str] = None
        for r in results:
            if not isinstance(r, tuple) or len(r) != 3:
                continue
            nid, hw, err = r
            if err and first_err is None:
                first_err = err
            if nid and hw:
                by_nid[nid] = hw

        for n in comp:
            nid = str(n.get("note_id") or "")
            if nid in by_nid:
                n["comment_hotwords_top10"] = by_nid[nid]

        # 逐条结果诊断
        success_ids = list(by_nid.keys())
        failed_notes = [n for n in chosen if str(n.get("note_id") or "") not in by_nid]
        failed_ids = [str(n.get("note_id") or "")[:12] for n in failed_notes]
        logger.info(
            "[comment_hotwords] result success={}/{} success_ids={} failed_ids={} first_err={}",
            len(by_nid),
            len(chosen),
            success_ids[:5],
            failed_ids[:5],
            first_err,
        )

        if by_nid:
            await self.emit_log(
                task_id,
                "info",
                f"竞品评论热词：已补全 {len(by_nid)} 条（首屏评论分词）",
            )
        elif chosen:
            err_hint = f" 接口提示：`{first_err}`。" if first_err else ""
            await self.emit_log(
                task_id,
                "warning",
                f"竞品评论热词：0 条成功（本批 {len(chosen)} 条）。{err_hint}"
                f"常见原因：① feed 补 xsec 仍失败（笔记下架/无权限）；② 国际站/国内站与 Cookie 不一致，需 .env 与 "
                f"`webapi` 同域；③ 本批需拉 {len(chosen)} 次评论接口，易触发限频，可将 "
                f"CRAWLER_COMPETITOR_COMMENT_MAX_NOTES 暂调小到 10～20 后重试。",
            )

    async def _write_back_l2(self, notes: List[Dict[str, Any]]) -> None:
        """L3 实时爬到结果后异步回写 L2，供下次向量检索使用。

        当前不带 embedding，避免额外调 embed 服务；仅靠关键词命中已足够支撑 L2 工作。
        """
        try:
            from ...infrastructure.storage.notes_vector_store import (
                get_notes_vector_store,
            )

            await get_notes_vector_store().add_notes(notes)
        except Exception:  # noqa: BLE001
            pass

    async def _build_result_from_cache(
        self,
        context: AgentContext,
        merged_notes: List[Dict[str, Any]],
        dims_keywords: Dict[str, List[str]],
        active_dims: List[str],
        runtime_cfg: Dict[str, Any],
        *,
        cache_source: str,
        cache_keywords: List[str],
        main_key: List[str],
        comp_key: List[str],
        main_layer: Optional[str],
        comp_layer: Optional[str],
        per_dim_sources: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        comp_missed_keywords: Optional[List[str]] = None,
    ) -> AgentResult:
        """命中缓存时返回 AgentResult；merged_notes 为去重合并池，per_dim_sources 非空时按维度侧池复制。"""
        task_id = context.task_id
        input_spec = context.task_context.get("input_spec") or {}

        samples_by_dim: Dict[str, List[Dict[str, Any]]] = {d: [] for d in _DIMENSIONS}
        for dim in active_dims:
            # 只有显式传入 None 时才使用 merged pool；空 dict 仍需按维度 .get，
            # 否则会把“无 per_dim”和“计算出空表”混在一起，导致 ti/tc 分池失效，
            # 最终错误复用 merged_notes，引发 Excel Sheet3/4/5 内容雷同。
            raw_src = (
                merged_notes
                if per_dim_sources is None
                else per_dim_sources.get(dim, merged_notes)
            )
            src = _filter_notes_for_dimension(raw_src, dims_keywords, dim)
            samples_by_dim[dim] = _copy_notes_for_dimension(src, dim, active_dims)

        if per_dim_sources is None:
            all_notes = list(merged_notes)
        else:
            by_id: Dict[str, Dict[str, Any]] = {}
            for dim in _DIMENSIONS:
                for note in samples_by_dim[dim]:
                    note_id = note.get("note_id", "")
                    key = note_id or f"__noid_{note.get('title', '')[:24]}_{note.get('keyword', '')}"
                    if key in by_id:
                        existing = by_id[key]
                        hits = existing.setdefault("dimensions_hit", [])
                        cur_dim = note.get("dimension")
                        if cur_dim and cur_dim not in hits:
                            hits.append(cur_dim)
                    else:
                        merged = dict(note)
                        cur_dim = note.get("dimension")
                        merged["dimensions_hit"] = [cur_dim] if cur_dim else []
                        by_id[key] = merged
            all_notes = list(by_id.values())
        for n in all_notes:
            n.setdefault("dimensions_hit", list(active_dims))
            _backfill_seo_top10_on_dict(n)

        dimension_status: Dict[str, str] = {
            d: ("cache" if d in active_dims else "empty") for d in _DIMENSIONS
        }

        notes_image = [n for n in all_notes if n.get("media_type") == "image"]
        notes_video = [n for n in all_notes if n.get("media_type") == "video"]

        base_keywords = list(
            (input_spec.get("parsed") or {}).get("keywords")
            or input_spec.get("keywords")
            or []
        )

        # 构造三源视图
        sources, all_notes_with_hits = _build_four_source_view(
            all_notes, samples_by_dim,
        )
        cookies_str = _resolve_cookies_str(_resolve_owner_user_id(task_id))

        if cookies_str and _CRAWLER_COMMENT_HW_ENABLED:
            await self._enrich_competitor_comment_hotwords(
                task_id, sources, cookies_str
            )

        # 互动量下限过滤
        _min_inter = int(runtime_cfg.get("min_interaction") or 0)
        if _min_inter:
            _before = len(all_notes_with_hits)
            all_notes_with_hits = _apply_min_interaction_filter(all_notes_with_hits, _min_inter)
            notes_image = _apply_min_interaction_filter(notes_image, _min_inter)
            notes_video = _apply_min_interaction_filter(notes_video, _min_inter)
            await self.emit_log(
                task_id, "info",
                f"互动量过滤（≥{_min_inter}）：{_before} → {len(all_notes_with_hits)} 条",
            )

        sample = {
            "source": "cache",
            "cache_source": cache_source,
            "sample_count": len(all_notes_with_hits),
            "keywords": base_keywords or [input_spec.get("raw_input", "")],
            "dimensions_keywords": dims_keywords,
            "samples_by_dimension": samples_by_dim,
            "notes_image": notes_image,
            "notes_video": notes_video,
            "note_summary": all_notes_with_hits,
            "dimension_status": dimension_status,
            # 4.3pre.2 新增
            "sources": sources,
            "all_notes": all_notes_with_hits,
            "competitor_cache_trace": _cache_trace_payload(
                cache_source=cache_source,
                cache_keywords=cache_keywords,
                main_key=main_key,
                comp_key=comp_key,
                main_layer=main_layer,
                comp_layer=comp_layer,
                comp_missed_keywords=comp_missed_keywords,
            ),
        }
        TaskContextWriter(context.task_context).write(
            "crawler_output", sample, agent_id=self.agent_id, note=f"cache_{cache_source}"
        )
        await self.emit_progress(
            task_id,
            f"采集完成（{cache_source}）：共 {len(all_notes)} 条 · "
            f"图文 {len(notes_image)} / 视频 {len(notes_video)}",
            progress=30,
        )
        return AgentResult(ok=True, produced_modules=self.provides, output=sample)

    async def _enrich_with_details(
        self,
        task_id: str,
        cookies_str: str,
        notes: List[Dict[str, Any]],
    ) -> None:
        """
        为采到的笔记并发补充精确互动数据（点赞 / 评论 / 收藏 / 分享）。

        与 MediaCrawler 一致的并发详情策略：
        - Semaphore 限制同时在飞的请求数（默认 3），避免高频触发风控
        - 只对互动量 Top-N 补（默认 30）
        - 每条启动间隔 interval（默认 0.4s），叠加 Semaphore 后输出更平滑
        - 单条超时独立，失败保留原值，不中断整体流程

        速度：30 条约 30s，相比之前串行 60s 左右，提速约 2x。
        """
        if not notes:
            return

        ranked = sorted(
            notes,
            key=lambda n: int(n.get("interaction_score") or 0),
            reverse=True,
        )[:_ENRICH_TOP_N]

        semaphore = asyncio.Semaphore(_ENRICH_MAX_CONCURRENCY)
        counters = {"enriched": 0, "attempted": 0}

        async def _one(note: Dict[str, Any]) -> None:
            url = note.get("url") or ""
            if not url:
                return
            counters["attempted"] += 1
            async with semaphore:
                try:
                    precise = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_note_detail_precise, cookies_str, url),
                        timeout=_ENRICH_PER_NOTE_TIMEOUT,
                    )
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    precise = None

            if precise:
                pa = (precise.get("published_at") or "").strip()
                if pa:
                    note["published_at"] = pa[:19]
                desc = precise.get("desc")
                if isinstance(desc, str) and len(desc) > len(
                    str(note.get("desc") or "")
                ):
                    note["desc"] = desc

                from ...services.note_seo_extract import extract_seo_top10_from_content

                note["seo_top10"] = extract_seo_top10_from_content(
                    title=str(note.get("title") or ""),
                    desc=str(note.get("desc") or ""),
                    tags=list(note.get("tags") or []),
                    media_type=str(note.get("media_type") or "image"),
                )

            if precise and (precise.get("likes") or precise.get("collects")):
                note["likes"] = precise.get("likes", note.get("likes") or 0)
                note["comments"] = precise.get("comments", note.get("comments") or 0)
                note["collects"] = precise.get("collects", note.get("collects") or 0)
                note["share_count"] = precise.get("shares", note.get("share_count") or 0)
                note["interaction_score"] = (
                    int(note["likes"])
                    + int(note["comments"])
                    + int(note["collects"])
                    + int(note["share_count"])
                )
                note["metrics_precise"] = True
                counters["enriched"] += 1

        # 用启动间隔模拟平滑出流，不是全部 gather 瞬间挤进去
        async def _with_startup_delay(idx: int, note: Dict[str, Any]) -> None:
            if idx > 0 and _ENRICH_INTERVAL_SEC > 0:
                await asyncio.sleep(_ENRICH_INTERVAL_SEC * idx / _ENRICH_MAX_CONCURRENCY)
            await _one(note)

        await asyncio.gather(
            *[_with_startup_delay(i, n) for i, n in enumerate(ranked)],
            return_exceptions=True,
        )

        await self.emit_log(
            task_id,
            "info",
            f"详情补充完成：{counters['enriched']}/{counters['attempted']} 条拿到精确互动数据",
        )


async def _collect_one_dimension(
    cookies_str: str,
    keywords: List[str],
    target_count: int,
    runtime_cfg: Dict[str, Any],
) -> List[Any]:
    """单维度采集：构造独立 collector 跑一次 multi_keywords。

    采集参数全部来自 runtime_cfg（前端高级配置），只在缺失时回落 env 默认。
    """
    from viral_agent.services.core.viral_collector import ViralNoteCollector

    collector = ViralNoteCollector(cookies_str)
    notes = await collector.search_viral_notes_multi_keywords(
        keywords=keywords[:_MAX_KWS_PER_DIM],
        target_count=target_count,
        note_type=runtime_cfg.get("note_type", 0),
        time_range=runtime_cfg.get("time_range", 0),
        min_sample_count=_MIN_SAMPLE,
    )
    return notes or []


def _normalize_note(note: Any, dimension: str, primary_keyword: str) -> Dict[str, Any]:
    """将 ViralNote 归一化为下游需要的 dict。

    media_type: image(图文) / video(视频)，其余字段按 downstream agents 需要输出。
    """
    from ...services.note_seo_extract import extract_seo_top10_from_content

    note_type = getattr(note, "note_type", "") or ""
    media_type = "video" if note_type == "视频" else "image"
    image_list = getattr(note, "image_list", None) or []
    cover = (
        getattr(note, "video_cover", None)
        or (image_list[0] if image_list else None)
    )

    published_at = str(getattr(note, "upload_time", "") or "").strip()
    tags = list(getattr(note, "tags", None) or [])
    sk = list(getattr(note, "source_keywords", []) or [])
    title = getattr(note, "title", "") or "(无标题)"
    desc = getattr(note, "desc", "") or ""
    seo_top10 = extract_seo_top10_from_content(
        title=title,
        desc=desc,
        tags=tags,
        media_type=media_type,
    )

    note_url = str(getattr(note, "note_url", "") or "")
    return {
        "note_id": getattr(note, "note_id", ""),
        "url": note_url,
        "note_url": note_url,
        # 从链接拆出，供评论热词等接口在 url 被截断时仍可取 token
        "xsec_token": _parse_xsec_token(note_url) if note_url else "",
        "title": title,
        "desc": desc,
        "likes": int(getattr(note, "liked_count", 0) or 0),
        "comments": int(getattr(note, "comment_count", 0) or 0),
        "collects": int(getattr(note, "collected_count", 0) or 0),
        "interaction_score": int(getattr(note, "interaction_score", 0) or 0),
        "media_type": media_type,
        "note_type": note_type,
        "cover_url": cover or "",
        "image_urls": list(image_list),
        "video_url": getattr(note, "video_addr", "") or "",
        "video_urls": getattr(note, "video_urls", []) or [],
        "nickname": getattr(note, "nickname", "") or "",
        "dimension": dimension,
        "keyword": primary_keyword,
        "source_keywords": sk,
        "tags": tags,
        "published_at": published_at,
        "seo_top10": seo_top10,
        "comment_hotwords_top10": [],
    }


def _parse_advanced_config(config: Any) -> Dict[str, Any]:
    """从前端高级配置 `advanced_config` 解析出后端采集参数。

    前端 UI（chat-panel.tsx）选项映射：
    - sample_count: "50" / "80" / "100" -> target_count (int)
    - note_type: "不限" / "视频" / "图文" -> 0 / 1 / 2
    - time_range: "不限" / "一天内" / "一周内" / "半年内" -> 0 / 1 / 2 / 3
    - min_interaction: "不限" / "1000+" / "5000+" / "10000+" -> 0 / 1000 / 5000 / 10000

    任一字段缺失或不合法时回落到 env 默认值，保证老流程不被破坏。
    """
    import re

    # 默认值（env 兜底；与前端 DEFAULT_ADVANCED 对齐：时间范围=半年内，互动量=1000+）
    out: Dict[str, Any] = {
        "target_count": _DEFAULT_TARGET_PER_GROUP,
        "note_type": 0,
        "time_range": 3,    # 半年内
        "min_interaction": 1000,
    }

    if not isinstance(config, dict):
        return out

    raw_count = config.get("sample_count")
    if raw_count is not None:
        try:
            value = int(str(raw_count).strip())
            if value > 0:
                out["target_count"] = max(5, min(500, value))  # 上限保护 500
        except (ValueError, TypeError):
            pass

    note_type_map = {"不限": 0, "视频": 1, "图文": 2}
    raw_note = config.get("note_type")
    if isinstance(raw_note, str) and raw_note in note_type_map:
        out["note_type"] = note_type_map[raw_note]

    time_range_map = {"不限": 0, "一天内": 1, "一周内": 2, "半年内": 3}
    raw_time = config.get("time_range")
    if isinstance(raw_time, str) and raw_time in time_range_map:
        out["time_range"] = time_range_map[raw_time]

    raw_inter = config.get("min_interaction")
    if raw_inter and str(raw_inter) != "不限":
        # 支持 "1000+" / "5000+" / "10000+" 或纯数字
        m_inter = re.search(r"(\d+)", str(raw_inter))
        if m_inter:
            out["min_interaction"] = int(m_inter.group(1))

    return out


def _apply_min_interaction_filter(
    notes: List[Dict[str, Any]],
    min_interaction: int,
) -> List[Dict[str, Any]]:
    """按互动量下限过滤笔记列表。min_interaction=0 时直接返回原列表。"""
    if not min_interaction:
        return notes
    return [
        n for n in notes
        if int(n.get("interaction_score") or 0) >= min_interaction
    ]


def _fetch_note_detail_precise(
    cookies_str: str, note_url: str
) -> Optional[Dict[str, Any]]:
    """同步调用详情接口，拿精确互动数据、发布时间和正文（用于热搜词抽取）。"""
    try:
        from apis.xhs_pc_apis import XHS_Apis
        from viral_agent.utils.number_utils import parse_chinese_number
        from xhs_utils.data_util import timestamp_to_str

        client = XHS_Apis()
        success, _msg, res = client.get_note_info(note_url, cookies_str)
        if not success or not isinstance(res, dict):
            return None

        items = (res.get("data") or {}).get("items") or []
        if not items:
            return None
        note_card = (items[0] or {}).get("note_card") or items[0] or {}
        interact = note_card.get("interact_info") or {}

        raw_time = note_card.get("time")
        if raw_time in (None, "", 0):
            raw_time = (items[0] or {}).get("create_time")
        published_at = ""
        if raw_time not in (None, "", 0):
            published_at = timestamp_to_str(raw_time)

        desc = str(note_card.get("desc") or "").strip()

        return {
            "likes": parse_chinese_number(interact.get("liked_count", 0)),
            "comments": parse_chinese_number(interact.get("comment_count", 0)),
            "collects": parse_chinese_number(interact.get("collected_count", 0)),
            "shares": parse_chinese_number(
                interact.get("shared_count") or interact.get("share_count") or 0
            ),
            "published_at": published_at,
            "desc": desc,
        }
    except Exception:  # noqa: BLE001
        return None


def _propagate_enriched_to_dim_samples(
    all_notes: List[Dict[str, Any]],
    samples_by_dim: Dict[str, List[Dict[str, Any]]],
) -> None:
    """把 all_notes 里被 detail 补充过的精确互动数据同步回 samples_by_dim。

    samples_by_dim 里的 dict 对象是 all_notes dict 的独立副本，必须显式拷贝精确字段过去，
    否则 InsightAgent 按维度读取时仍然会看到旧的 `100+` 下界值。
    """
    by_id = {n.get("note_id"): n for n in all_notes if n.get("note_id")}
    for dim_notes in samples_by_dim.values():
        for sample in dim_notes:
            source = by_id.get(sample.get("note_id"))
            if source and source.get("metrics_precise"):
                sample["likes"] = source["likes"]
                sample["comments"] = source["comments"]
                sample["collects"] = source["collects"]
                sample["share_count"] = source.get("share_count", 0)
                sample["interaction_score"] = source["interaction_score"]
                sample["metrics_precise"] = True
            if source:
                pa = str(source.get("published_at") or "").strip()
                if pa:
                    sample["published_at"] = pa[:19]
                if source.get("seo_top10"):
                    sample["seo_top10"] = list(source["seo_top10"])
                sdesc = source.get("desc")
                if isinstance(sdesc, str) and len(sdesc) > len(
                    str(sample.get("desc") or "")
                ):
                    sample["desc"] = sdesc


# ============================================================
# 阶段 4.3pre.2: 四源视图构造，对齐抗老精华模板 Sheet 3/4/5/6
# ============================================================
# 不额外调用 XHS API，而是把现有三维采集结果映射到四源视图，供
# ViralModelAgent / Insight / CanvasRender 消费新协议。
#
# 当前映射规则（三维 -> 三源）：
# - industry   -> category_top（品类样本）
# - competitor -> competitor（竞品样本）
# - brand      -> top_interaction（本品样本）

def _safe_env_int(key: str, default: int) -> int:
    """避免 .env 里写成空串或非数字时 int() 在 import 阶段炸掉整个 worker。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        return max(0, int(s, 10))
    except ValueError:
        return default


def _competitor_comment_max_notes_from_env() -> int:
    """竞品「评论热词 Top10」最多对多少条笔记调评论接口；每次执行时读取 `os.environ`，由 .env / 部署环境注入。

    环境变量：`CRAWLER_COMPETITOR_COMMENT_MAX_NOTES`（未设置或非法时默认 50）。
    """
    return _safe_env_int("CRAWLER_COMPETITOR_COMMENT_MAX_NOTES", 50)


_TOP_INTERACTION_N = _safe_env_int("CRAWLER_TOP_INTERACTION_N", 50)
_CRAWLER_COMMENT_HW_ENABLED = os.getenv(
    "CRAWLER_COMPETITOR_COMMENT_HOTWORDS", "true"
).strip().lower() in ("1", "true", "yes")
# 竞品 Sheet「评论热词 Top10」条数上限见 `_competitor_comment_max_notes_from_env()`（CRAWLER_COMPETITOR_COMMENT_MAX_NOTES）。


def _interaction_sort_key(n: Dict[str, Any]) -> float:
    return float(
        int(n.get("interaction_score") or 0) + int(n.get("likes") or 0) * 1.5
    )


def _published_ts(note: Dict[str, Any]) -> float:
    raw = str(note.get("published_at") or "").strip()
    if not raw:
        return 0.0
    head = raw[:19].replace("/", "-")
    try:
        return datetime.fromisoformat(head.replace(" ", "T")).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(raw[:10].replace("/", "-"), "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0


def _backfill_seo_top10_on_dict(n: Dict[str, Any]) -> None:
    """缓存命中等路径下，按标题/正文/标签重算 seo_top10，和 L3 结果对齐。"""
    from ...services.note_seo_extract import extract_seo_top10_from_content

    tags = n.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    n["seo_top10"] = extract_seo_top10_from_content(
        title=str(n.get("title") or ""),
        desc=str(n.get("desc") or ""),
        tags=list(tags),
        media_type=str(n.get("media_type") or "image"),
    )


def _parse_xsec_token(note_url: str) -> str:
    try:
        q = parse_qs(urlparse(note_url).query)
        vals = q.get("xsec_token") or []
        return str(vals[0]).strip() if vals else ""
    except Exception:  # noqa: BLE001
        return ""


def _note_url_candidates(note: Dict[str, Any]) -> str:
    """与归一化字段对齐：url / note_url / share_url。"""
    return str(
        note.get("url")
        or note.get("note_url")
        or note.get("share_url")
        or ""
    ).strip()


def _resolve_xsec_for_comment_api(note: Dict[str, Any]) -> str:
    """评论接口必填 xsec：优先从链接 query 取，再尝试顶层字段（部分缓存/中间结构会拆出 token）。"""
    u = _note_url_candidates(note)
    xs = _parse_xsec_token(u)
    note_id = str(note.get("note_id") or "")[:12]
    if xs:
        logger.debug(
            "[comment_hotwords] xsec_resolve note_id={} source=url ok", note_id
        )
        return xs
    for key in ("xsec_token", "xsecToken"):
        v = note.get(key)
        if v is not None and str(v).strip():
            logger.debug(
                "[comment_hotwords] xsec_resolve note_id={} source={} ok", note_id, key
            )
            return str(v).strip()
    logger.debug(
        "[comment_hotwords] xsec_resolve note_id={} url_preview={} miss",
        note_id,
        u[:80] if u else "EMPTY",
    )
    return ""


def _comment_root_list_from_data(data: Any) -> List[Any]:
    """评论分页 `data` 节可能使用 comments / list / comment_list 等字段名。"""
    if not isinstance(data, dict):
        return []
    for key in ("comments", "comment_list", "list", "items"):
        raw = data.get(key)
        if isinstance(raw, list) and raw:
            return raw
    return []


def _comment_texts_from_api_payload(comments: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(comments, list):
        return out
    for c in comments:
        if not isinstance(c, dict):
            continue
        content = c.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            out.append(text)
    return out


def _hotwords_from_comment_texts(texts: List[str], top_n: int = 10) -> List[str]:
    if not texts:
        return []
    blob = "\n".join(texts[:50])
    try:
        import jieba

        tokens = [w.strip() for w in jieba.cut(blob) if len(w.strip()) >= 2]
    except Exception:  # noqa: BLE001
        tokens = re.findall(r"[\u4e00-\u9fff]{2,5}", blob)
    tiny_stop = {
        "怎么",
        "什么",
        "这个",
        "那个",
        "可以",
        "一个",
        "没有",
        "不是",
        "真的",
        "感觉",
        "这样",
        "那种",
        "作者",
        "回复",
        "笔记",
        "视频",
        "图片",
    }
    c = Counter(t for t in tokens if t not in tiny_stop)
    return [w for w, _ in c.most_common(top_n)]


def _lazy_fetch_xsec_token_sync(note_id: str, cookies_str: str) -> str:
    """搜索列表无 xsec 时，用 feed 接口补一条可用的 xsec_token（与 ViralNoteCollector 逻辑对齐）。"""
    if not note_id or not cookies_str:
        return ""
    try:
        from apis.xhs_pc_apis import XHS_Apis
        from xhs_utils.xhs_util import xhs_web_origin

        url = f"{xhs_web_origin().rstrip('/')}/explore/{note_id}?xsec_source=pc_feed"
        client = XHS_Apis()
        success, msg, res_json = client.get_note_info(url, cookies_str)
        if not success or not isinstance(res_json, dict):
            logger.info(
                "[comment_hotwords] xsec_feed note_id={} success={} msg={} "
                "res_type={}",
                note_id,
                success,
                msg,
                type(res_json).__name__,
            )
            return ""
        items = (res_json.get("data") or {}).get("items") or []
        if not items:
            logger.info(
                "[comment_hotwords] xsec_feed note_id={} items_empty data_keys={}",
                note_id,
                list((res_json.get("data") or {}).keys()),
            )
            return ""
        item0 = items[0]
        if not isinstance(item0, dict):
            logger.info(
                "[comment_hotwords] xsec_feed note_id={} item0_type={}",
                note_id,
                type(item0).__name__,
            )
            return ""
        xs = str(item0.get("xsec_token") or "").strip()
        logger.info(
            "[comment_hotwords] xsec_feed note_id={} got={} len={}",
            note_id,
            bool(xs),
            len(xs),
        )
        return xs
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "[comment_hotwords] xsec_feed note_id={} exception={}", note_id, exc
        )
        return ""


def _note_apply_xsec(
    note: Dict[str, Any], note_id: str, xsec: str, *, xsec_source: str = "pc_feed"
) -> None:
    """把补到的 token 写回 dict，便于同任务内后续步骤复用。"""
    note["xsec_token"] = xsec
    u = _note_url_candidates(note)
    if not u or not _parse_xsec_token(u):
        from xhs_utils.xhs_util import xhs_web_origin

        built = (
            f"{xhs_web_origin().rstrip('/')}/explore/{note_id}"
            f"?xsec_token={xsec}&xsec_source={xsec_source}"
        )
        note["url"] = built
        note["note_url"] = built


def _fetch_comment_hotwords_sync(
    note: Dict[str, Any], cookies_str: str
) -> Tuple[List[str], Optional[str]]:
    """返回 (热词列表, 错误说明)。错误仅用于诊断日志，成功时为 None。"""
    note_id = str(note.get("note_id") or "").strip()
    if not note_id:
        return [], "缺少 note_id"
    if not cookies_str:
        return [], "缺少 cookies"
    if note_id.startswith("stub_"):
        return [], None
    resolver_kind = "url"
    xsec = _resolve_xsec_for_comment_api(note)
    if not xsec:
        resolver_kind = "feed"
        xsec = _lazy_fetch_xsec_token_sync(note_id, cookies_str)
        if xsec:
            _note_apply_xsec(note, note_id, xsec)
    if not xsec:
        logger.warning(
            "[comment_hotwords] note_id={} xsec_fail source=url+feed", note_id
        )
        return [], "无法解析 xsec_token（feed 补全失败或笔记不可用）"
    try:
        from apis.xhs_pc_apis import XHS_Apis

        client = XHS_Apis()
        # 与 MediaCrawler get_note_comments 一致：仅 note_id/cursor/image_formats/xsec_token，GET 签名字段序与逗号编码见 splice_str_get_xhs
        success, msg, res_json = client.get_note_out_comment(
            note_id, "", xsec, cookies_str
        )
        if not success or not isinstance(res_json, dict):
            res_preview = ""
            code = None
            if isinstance(res_json, dict):
                code = res_json.get("code")
                res_preview = str(
                    {k: v for k, v in res_json.items() if k not in ("data", "items")}
                )[:300]
            elif res_json is not None:
                res_preview = str(res_json)[:300]
            logger.warning(
                "[comment_hotwords] note_id={} comment_api_fail success={} msg={} "
                "code={} res_preview={}",
                note_id,
                success,
                msg,
                code,
                res_preview,
            )
            err_tail = (msg or "comment/page 请求失败")[:500]
            if code is not None:
                err_tail = f"{err_tail} code={code}"
            return [], err_tail
        data = res_json.get("data") or {}
        comments = _comment_root_list_from_data(data)
        texts = _comment_texts_from_api_payload(comments)
        hotwords = _hotwords_from_comment_texts(texts, top_n=10)
        logger.info(
            "[comment_hotwords] note_id={} resolver={} "
            "comments_raw={} texts_extracted={} hotwords={} hotwords_preview={}",
            note_id,
            resolver_kind,
            len(comments),
            len(texts),
            len(hotwords),
            hotwords[:5],
        )
        return hotwords, None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[comment_hotwords] note_id={} exception={}", note_id, exc
        )
        return [], str(exc)[:500]


def _build_four_source_view(
    all_notes: List[Dict[str, Any]],
    samples_by_dim: Dict[str, List[Dict[str, Any]]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """把三维采集结果映射成三源视图。

    Returns:
        sources: {"category_top": [...], "competitor": [...], "top_interaction": [...]}
        all_notes_with_hits: 每条 note 附加 sources_hit: List[str]，标明命中了哪些来源

    Note:
    - top_interaction: 优先 brand 维度样本；brand 为空时回退为 industry(category_top) 互动 Top
    - sources_hit 与模板 A 列“来源”标签对齐，可多选
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for n in all_notes:
        nid = n.get("note_id") or f"_noid_{n.get('title', '')[:16]}"
        by_id[nid] = n
        n.setdefault("sources_hit", [])

    sources: Dict[str, List[Dict[str, Any]]] = {
        "category_top": [],
        "competitor": [],
        "top_interaction": [],
    }

    # industry / competitor 两源（与样本维度一一对应）
    for dim, source_name in (("industry", "category_top"), ("competitor", "competitor")):
        dim_notes = samples_by_dim.get(dim) or []
        for note in dim_notes:
            nid = note.get("note_id") or f"_noid_{note.get('title', '')[:16]}"
            canonical = by_id.get(nid, note)
            if source_name not in canonical["sources_hit"]:
                canonical["sources_hit"].append(source_name)
            sources[source_name].append(canonical)

    # brand -> top_interaction（本品样本）
    brand_notes: List[Dict[str, Any]] = []
    for note in samples_by_dim.get("brand") or []:
        nid = note.get("note_id") or f"_noid_{note.get('title', '')[:16]}"
        brand_notes.append(by_id.get(nid, note))
    brand_sorted = sorted(brand_notes, key=_interaction_sort_key, reverse=True)
    for note in brand_sorted[:_TOP_INTERACTION_N]:
        if "top_interaction" not in note["sources_hit"]:
            note["sources_hit"].append("top_interaction")
        sources["top_interaction"].append(note)

    if not sources["top_interaction"]:
        # brand 维度为空，优先从 industry(category_top) 中按互动排序取 top N，
        # 避免与 all_notes 全量重叠（all_notes 包含 category_top 所有笔记）
        industry_pool: List[Dict[str, Any]] = []
        for note in samples_by_dim.get("industry") or []:
            nid = note.get("note_id") or f"_noid_{note.get('title', '')[:16]}"
            industry_pool.append(by_id.get(nid, note))
        fallback_pool = sorted(industry_pool, key=_interaction_sort_key, reverse=True) if industry_pool else sorted(all_notes, key=_interaction_sort_key, reverse=True)
        for note in fallback_pool[:_TOP_INTERACTION_N]:
            if "top_interaction" not in note["sources_hit"]:
                note["sources_hit"].append("top_interaction")
            sources["top_interaction"].append(note)

    return sources, list(by_id.values())



def _build_stub(
    dims_keywords: Dict[str, List[str]],
    active_dims: List[str],
) -> tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """全部失败时返回 stub，保证画布仍可生成。"""
    from ...services.note_seo_extract import extract_seo_top10_from_content

    samples_by_dim: Dict[str, List[Dict[str, Any]]] = {d: [] for d in _DIMENSIONS}
    all_notes: List[Dict[str, Any]] = []
    for dim in active_dims or _DIMENSIONS:
        for idx, kw in enumerate(dims_keywords.get(dim) or ["默认品类"]):
            mt = "image" if idx % 2 == 0 else "video"
            title = f"[{dim}] 示例爆款 - {kw}"
            desc = f"stub 文案：围绕 {kw} 的使用体验与效果对比，适合作为占位内容。"
            tags = [f"stub标签{dim}{idx}"]
            item = {
                "note_id": f"stub_{dim}_{idx}",
                "url": "",
                "title": title,
                "desc": desc,
                "likes": 10000 + idx * 500,
                "comments": 250 + idx * 15,
                "collects": 800 + idx * 50,
                "interaction_score": 10000 + idx * 700,
                "media_type": mt,
                "note_type": "图文" if idx % 2 == 0 else "视频",
                "cover_url": "",
                "image_urls": [],
                "video_url": "",
                "nickname": "",
                "dimension": dim,
                "dimensions_hit": [dim],
                "keyword": kw,
                "source_keywords": [kw],
                "tags": tags,
                "published_at": f"2025-0{(idx % 6) + 1}-15",
                "seo_top10": extract_seo_top10_from_content(
                    title=title, desc=desc, tags=tags, media_type=mt
                ),
                "comment_hotwords_top10": [],
            }
            samples_by_dim[dim].append(item)
            all_notes.append(item)
    return samples_by_dim, all_notes


def _collect_cache_keywords(
    dims_keywords: Dict[str, List[str]],
    active_dims: List[str],
) -> List[str]:
    """把三维度关键词合并去重后排序，作为 L1/L2 缓存的统一 key。

    同一组关键词无论写在哪个维度里，合并排序后哈希结果一致，保证命中率。
    """
    collected: List[str] = []
    for dim in active_dims:
        collected.extend(dims_keywords.get(dim) or [])
    normalized = sorted({str(k).strip() for k in collected if str(k).strip()})
    return normalized


def _resolve_owner_user_id(task_id: str) -> str:
    try:
        record = task_repository.get(task_id)
        if record and getattr(record, "owner_user_id", None):
            return str(record.owner_user_id)
    except Exception:
        pass
    return ""


def _resolve_cookies_str(owner_user_id: str) -> str:
    """按优先级查找可用 cookie（Phase 1 重构：委托 XhsCredentialResolver）。

    顺序（详见 :mod:`backend.app.services.xhs_auth.credential_resolver`）：

    1. ``XHS_COOKIES_OVERRIDE`` 环境变量
    2. :class:`XhsCredentialStore` 中按 RedMuse ``user_id`` 命中的记录
    3. 兼容旧 XHS user_id（identity_store 反查 username）
    4. ``ALLOW_ADMIN_COOKIE_FALLBACK=true`` 时回退 admin
    5. ``COOKIES`` / ``COOKIE`` 环境变量（仅非 u_* 用户可用）

    返回结构化结果只保留 cookie 字符串，调用方维持原签名不变。
    """
    from ...services.xhs_auth import get_credential_resolver

    resolved = get_credential_resolver().resolve(owner_user_id)
    if resolved.found:
        logger.info(
            f"[cookie] owner={owner_user_id!r} source={resolved.source} "
            f"path={resolved.cookies_path} len={len(resolved.cookies_str)}"
        )
        return resolved.cookies_str

    # u_* 用户未绑定 XHS 账号时，抛出明确错误，禁止使用空 cookie 继续爬取
    if owner_user_id and owner_user_id.startswith("u_"):
        raise RuntimeError(
            f"用户 {owner_user_id} 尚未绑定小红书账号，无法执行数据采集任务。"
            "请到「设置 → 数据源授权」完成小红书账号绑定后再试。"
        )

    return ""
