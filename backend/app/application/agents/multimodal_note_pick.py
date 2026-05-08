"""多模态 6 要素标注：选取待分析笔记(图文/视频共用)。

Excel 与画布的四源样本表会展示 `crawler_output.sources` 里的全部行;若标注阶段
只按全池 Top-N 取样,会出现「表里有这条,但 6 要素/内容方向全空」。

策略:
- 先覆盖所有会出现在样本表中的笔记(media_type 匹配且在 notes_pool 内),
  按互动分排序;
- 若数量仍低于 floor_n,再从全池高分补足;
- 总量不超过 hard_max(成本控制)。

环境变量由各 Agent 读取后传入 floor_n / hard_max。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 与 CrawlerAgent._build_four_source_view 的四个桶一致
_SOURCE_BUCKETS = ("category_top", "competitor", "top_interaction", "serp_top")


def _score(note: Dict[str, Any]) -> float:
    return float(
        int(note.get("interaction_score") or 0) + int(note.get("likes") or 0) * 1.5
    )


def visible_note_ids_for_media(crawler: Dict[str, Any], media_type: str) -> List[str]:
    """四源样本表里会出现的 note_id(去重,按桶顺序稳定排列)."""
    sources = crawler.get("sources") or {}
    order: List[str] = []
    seen: set[str] = set()
    for bucket in _SOURCE_BUCKETS:
        for n in sources.get(bucket) or []:
            if (n.get("media_type") or "").strip() != media_type:
                continue
            nid = str(n.get("note_id") or "").strip()
            if not nid or nid in seen:
                continue
            seen.add(nid)
            order.append(nid)
    return order


def pick_multimodal_notes(
    notes_pool: List[Dict[str, Any]],
    crawler: Dict[str, Any],
    *,
    media_type: str,
    floor_n: int,
    hard_max: int,
) -> List[Dict[str, Any]]:
    """返回本轮要送 LLM 的笔记列表(池内 dict 引用,含 video_url/cover_url 等完整字段)。"""
    by_pool: Dict[str, Dict[str, Any]] = {}
    for n in notes_pool:
        nid = str(n.get("note_id") or "").strip()
        if nid:
            by_pool[nid] = n

    visible_ids = visible_note_ids_for_media(crawler, media_type)
    visible_notes = [by_pool[i] for i in visible_ids if i in by_pool]
    visible_sorted = sorted(visible_notes, key=_score, reverse=True)
    vis_set = {str(n.get("note_id") or "").strip() for n in visible_sorted}

    pool_sorted = sorted(notes_pool, key=_score, reverse=True)
    rest = [
        n
        for n in pool_sorted
        if str(n.get("note_id") or "").strip() not in vis_set
    ]

    if hard_max <= 0:
        return []
    target = min(hard_max, max(floor_n, len(visible_sorted)))

    out: List[Dict[str, Any]] = []
    seen_out: set[str] = set()
    for n in visible_sorted:
        nid = str(n.get("note_id") or "").strip()
        if nid and nid not in seen_out:
            out.append(n)
            seen_out.add(nid)
        if len(out) >= target:
            return out
    for n in rest:
        nid = str(n.get("note_id") or "").strip()
        if nid and nid not in seen_out:
            out.append(n)
            seen_out.add(nid)
        if len(out) >= target:
            break
    return out
