"""小红书数据采集类工具。

细粒度：search_notes / fetch_note_detail / fetch_comments
批量/宏  ：collect_notes（按目标数量采集，复用 DB 缓存 + 去重）/ fetch_notes_details

底座为三方 Redbook API（RedbookApiClient，同步 → asyncio.to_thread 转异步）。
采集类均标注 cost="expensive"，受 AgentRuntime 单位制预算（expensive_unit_budget）约束；
完整结果写入 RunBlackboard，只把"紧凑摘要 + 句柄"回注模型，避免上下文爆炸。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ....infrastructure.crawlers.redbook_api_client import RedbookApiClient
from ..blackboard import PREVIEW_DEFAULT, get_blackboard
from ..tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


_SORT_MAP = {
    "general": "general",
    "综合": "general",
    "latest": "time_descending",
    "最新": "time_descending",
    "time_descending": "time_descending",
}
_NOTE_TYPE_MAP = {"all": 0, "全部": 0, "video": 1, "视频": 1, "image": 2, "图文": 2}

# collect_notes 护栏：单次工具内的采集规模上限（防止 runaway / 超时）
_MAX_SEARCH_PAGES_TOTAL = 15   # 单次 collect_notes 最多翻多少页搜索
_MAX_DETAIL_IN_COLLECT = 15    # with_detail 时最多补全多少条详情
_TARGET_HARD_CAP = 300         # target_count 硬上限


def _client() -> RedbookApiClient:
    return RedbookApiClient()


def _parse_int(value: Any, default: int = 0) -> int:
    """从 int / "1000+" / "约200条" 等抽取整数。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group())
    return default


def _trim_note(note: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪笔记字段，回注模型预览时控制 token。"""
    return {
        "note_id": note.get("note_id"),
        "title": note.get("title"),
        "desc": (note.get("desc") or "")[:200],
        "likes": note.get("likes"),
        "collects": note.get("collects"),
        "comments": note.get("comments"),
        "interaction_score": note.get("interaction_score"),
        "publish_time": note.get("publish_time"),
        "note_type": note.get("note_type"),
        "nickname": note.get("nickname"),
        "url": note.get("url"),
    }


def _normalize_note(note: Dict[str, Any]) -> Dict[str, Any]:
    """统一笔记字段（兼容搜索结果与 DB 缓存行）。"""
    return {
        "note_id": str(note.get("note_id") or "").strip(),
        "title": note.get("title") or "",
        "desc": note.get("desc") or note.get("description") or "",
        "tags": note.get("tags") or note.get("tag_list") or [],
        "likes": int(note.get("likes") or 0),
        "collects": int(note.get("collects") or 0),
        "comments": int(note.get("comments") or 0),
        "interaction_score": int(
            note.get("interaction_score")
            or (int(note.get("likes") or 0) + int(note.get("collects") or 0) + int(note.get("comments") or 0))
        ),
        "publish_time": note.get("publish_time") or "",
        "note_type": note.get("note_type") or "",
        "nickname": note.get("nickname") or "",
        "url": note.get("url") or note.get("note_url") or "",
        "source_keyword": note.get("source_keyword") or note.get("keyword") or "",
    }


def _preview_block(notes: List[Dict[str, Any]], handle: str, summary: str, *, extra_hint: str = "") -> str:
    fields = list(_trim_note(notes[0]).keys()) if notes else []
    preview = [_trim_note(n) for n in sorted(notes, key=lambda n: n.get("interaction_score", 0), reverse=True)[:PREVIEW_DEFAULT]]
    hint = extra_hint or f'如需筛选/排序/取全部，调用 query_dataset(handle="{handle}", sort_by="interaction_score", ...)。'
    return (
        f"{summary} 已存入工作记忆 handle={handle}。\n"
        f"可用字段：{', '.join(fields)}\n"
        f"预览（前 {len(preview)} 条，按互动量降序）：\n{json.dumps(preview, ensure_ascii=False)}\n{hint}"
    )


def _clean_keywords(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[:5]


# ── 细粒度工具 ─────────────────────────────────────────────────────────────
async def _search_notes(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    keyword = str(args.get("keyword") or "").strip()
    if not keyword:
        return ToolResult(ok=False, content="search_notes 需要 keyword 参数", error="MISSING_ARGS")
    page = int(args.get("page") or 1)
    sort = _SORT_MAP.get(str(args.get("sort") or "general").lower(), "general")
    note_type = _NOTE_TYPE_MAP.get(str(args.get("note_type") or "all").lower(), 0)
    limit = max(1, min(int(args.get("limit") or 20), 30))

    client = _client()
    resp = await asyncio.to_thread(client.search, keyword, page, sort, note_type, _max_retries=2)
    api_err = client.extract_api_error(resp)
    if api_err:
        logger.warning("[tool.search_notes] 采集接口业务错误：{}", api_err)
        return ToolResult(
            ok=False,
            content=f"采集接口暂时不可用（第三方返回错误：{api_err}）。这通常是数据源服务异常或账号受限，"
                    f"非关键词问题；可稍后重试，或先基于知识库/联网检索的信息作答。",
            error="UPSTREAM_API_ERROR",
            display="采集接口异常",
            units=1,
        )
    raw_notes = client.parse_search_notes(resp)
    notes = [_normalize_note(client.note_to_dict(n, keyword)) for n in raw_notes][:limit]
    summary = f"关键词「{keyword}」第 {page} 页搜索到 {len(notes)} 条笔记（sort={sort}）。"
    logger.info("[tool.search_notes] {}", summary)
    if not notes:
        return ToolResult(ok=True, content=summary + " 无结果。", data={"keyword": keyword, "notes": []}, display=summary, units=1)
    bb = get_blackboard(ctx)
    handle = bb.put_dataset("notes", notes, meta={"keyword": keyword, "page": page, "sort": sort})
    return ToolResult(
        ok=True,
        content=_preview_block(notes, handle, summary),
        data={"keyword": keyword, "page": page, "handle": handle, "count": len(notes)},
        display=summary,
        units=1,
    )


async def _fetch_note_detail(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    note_id = str(args.get("note_id") or "").strip()
    if not note_id:
        return ToolResult(ok=False, content="fetch_note_detail 需要 note_id 参数", error="MISSING_ARGS")
    client = _client()
    resp = await asyncio.to_thread(client.get_detail, note_id, _max_retries=1)
    api_err = client.extract_api_error(resp)
    if api_err:
        return ToolResult(
            ok=False,
            content=f"详情接口暂时不可用（第三方返回错误：{api_err}）。",
            error="UPSTREAM_API_ERROR",
            display="详情接口异常",
            units=1,
        )
    detail = client.parse_detail_note(resp)
    if not detail:
        return ToolResult(ok=False, content=f"笔记 {note_id} 未取到详情（可能已删除或 token 过期）", error="NO_DETAIL", units=1)
    fields = client.extract_detail_fields(detail)
    desc = fields.get("desc", "")
    payload = {"note_id": note_id, "desc": desc, "tags": fields.get("tags", [])}
    bb = get_blackboard(ctx)
    handle = bb.put_dataset("details", [payload], meta={"note_id": note_id})
    display = f"笔记 {note_id} 详情已获取（正文 {len(desc)} 字，{len(payload['tags'])} 个话题标签）"
    # 单条详情正文一般不长，直接回注；超长则截断并提示用 query_dataset 取全文
    desc_for_model = desc if len(desc) <= 1800 else desc[:1800] + f"\n…（全文 {len(desc)} 字，已存 handle={handle}）"
    content = display + "\n" + json.dumps({"desc": desc_for_model, "tags": payload["tags"], "handle": handle}, ensure_ascii=False)
    return ToolResult(ok=True, content=content, data=payload, display=display, units=1)


async def _fetch_comments(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    note_id = str(args.get("note_id") or "").strip()
    if not note_id:
        return ToolResult(ok=False, content="fetch_comments 需要 note_id 参数", error="MISSING_ARGS")
    max_comments = max(1, min(int(args.get("max_comments") or 50), 200))
    max_pages = max(1, min(int(args.get("max_pages") or 2), 15))

    client = _client()
    collected: List[Dict[str, Any]] = []
    cursor = ""
    pages_called = 0
    for page_idx in range(max_pages):
        resp = await asyncio.to_thread(client.get_comments, note_id, cursor, _max_retries=1)
        pages_called += 1
        api_err = client.extract_api_error(resp)
        if api_err:
            if page_idx == 0:
                return ToolResult(
                    ok=False,
                    content=f"评论接口暂时不可用（第三方返回错误：{api_err}）。",
                    error="UPSTREAM_API_ERROR",
                    display="评论接口异常",
                    units=pages_called,
                )
            logger.warning("[tool.fetch_comments] 第 {} 页业务错误，停止翻页：{}", page_idx + 1, api_err)
            break
        raw, has_more, next_cursor, _count = client.parse_comments(resp)
        for c in raw:
            item = client.parse_comment_item(c, note_id=note_id, note_url="", note_title="", is_sub=False, parent_id="")
            if item:
                collected.append(
                    {
                        "content": item["content"],
                        "like_count": item["like_count"],
                        "author": item["author"],
                        "ip_location": item["ip_location"],
                    }
                )
        if len(collected) >= max_comments or not has_more or not next_cursor:
            break
        cursor = next_cursor

    collected = collected[:max_comments]
    summary = f"笔记 {note_id} 采集到 {len(collected)} 条评论"
    if not collected:
        return ToolResult(ok=True, content=summary + "（无评论）", data={"note_id": note_id, "comments": []}, display=summary, units=pages_called)
    bb = get_blackboard(ctx)
    handle = bb.put_dataset("comments", collected, meta={"note_id": note_id})
    preview = sorted(collected, key=lambda c: c.get("like_count", 0), reverse=True)[:PREVIEW_DEFAULT]
    content = (
        f"{summary}。已存入工作记忆 handle={handle}。\n"
        f"可用字段：content, like_count, author, ip_location\n"
        f"高赞预览（前 {len(preview)} 条）：\n{json.dumps(preview, ensure_ascii=False)}\n"
        f'如需全部或筛选，调用 query_dataset(handle="{handle}", sort_by="like_count", ...)。'
    )
    return ToolResult(ok=True, content=content, data={"note_id": note_id, "handle": handle, "count": len(collected)}, display=summary, units=pages_called)


# ── 批量 / 宏工具 ───────────────────────────────────────────────────────────
async def _fetch_details_for(note_ids: List[str], *, jitter: bool = True) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """批量补全详情，返回 (note_id -> {desc, tags}, units)。失败的笔记跳过（fail-open）。"""
    client = _client()
    out: Dict[str, Dict[str, Any]] = {}
    units = 0
    for nid in note_ids:
        try:
            if jitter and units > 0:
                await asyncio.sleep(random.uniform(0.3, 0.7))
            resp = await asyncio.to_thread(client.get_detail, nid, _max_retries=1)
            units += 1
            if client.extract_api_error(resp):
                continue
            detail = client.parse_detail_note(resp)
            if not detail:
                continue
            fields = client.extract_detail_fields(detail)
            out[nid] = {"desc": fields.get("desc", ""), "tags": fields.get("tags", [])}
        except Exception as exc:  # noqa: BLE001 —— 单条详情失败不影响整体
            logger.warning("[tool.fetch_notes_details] note {} 失败: {}", nid, exc)
    return out, units


async def _collect_notes(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    keywords = _clean_keywords(args.get("keywords") or args.get("keyword"))
    if not keywords:
        return ToolResult(ok=False, content="collect_notes 需要 keywords（关键词数组或字符串）", error="MISSING_ARGS")

    adv = ctx.advanced_config or {}
    target = _parse_int(args.get("target_count"), _parse_int(adv.get("sample_count"), 50))
    target = max(1, min(target, _TARGET_HARD_CAP))
    sort = _SORT_MAP.get(str(args.get("sort") or "general").lower(), "general")
    note_type = _NOTE_TYPE_MAP.get(str(args.get("note_type") or "all").lower(), 0)
    min_interaction = _parse_int(args.get("min_interaction"), _parse_int(adv.get("min_interaction"), 0))
    with_detail = bool(args.get("with_detail", False))

    seen: Dict[str, Dict[str, Any]] = {}

    # 1) 先用 DB 缓存命中（跨 cache_key，按 note_id 去重）
    cached_count = 0
    try:
        from ....infrastructure.storage.comment_cache_store import comment_cache_store

        cached_notes, _n = await comment_cache_store.load_notes_for_keywords(keywords)
        for raw in cached_notes:
            note = _normalize_note(raw)
            nid = note["note_id"]
            if not nid or nid in seen:
                continue
            if min_interaction and note["interaction_score"] < min_interaction:
                continue
            seen[nid] = note
            cached_count += 1
    except Exception as exc:  # noqa: BLE001 —— 缓存不可用时降级为纯实时采集
        logger.warning("[tool.collect_notes] 读取缓存失败（降级实时采集）: {}", exc)

    units = 0
    # 2) 不足目标数则实时补采（按关键词翻页，去重）
    if len([n for n in seen.values()]) < target:
        client = _client()
        pages_total = 0
        for kw in keywords:
            if len(seen) >= target or pages_total >= _MAX_SEARCH_PAGES_TOTAL:
                break
            page = 1
            empty_streak = 0
            while len(seen) < target and pages_total < _MAX_SEARCH_PAGES_TOTAL:
                try:
                    resp = await asyncio.to_thread(client.search, kw, page, sort, note_type, _max_retries=2)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[tool.collect_notes] 搜索 {} 第 {} 页失败: {}", kw, page, exc)
                    break
                pages_total += 1
                units += 1
                api_err = client.extract_api_error(resp)
                if api_err:
                    # 首个关键词首页就报错 → 整体不可用；否则跳过该关键词
                    if units == 1 and cached_count == 0:
                        return ToolResult(
                            ok=False,
                            content=f"采集接口暂时不可用（第三方返回错误：{api_err}）。可稍后重试或先用已有信息作答。",
                            error="UPSTREAM_API_ERROR",
                            display="采集接口异常",
                            units=units,
                        )
                    logger.warning("[tool.collect_notes] 关键词 {} 业务错误，跳过：{}", kw, api_err)
                    break
                raw_notes = client.parse_search_notes(resp)
                added = 0
                for rn in raw_notes:
                    note = _normalize_note(client.note_to_dict(rn, kw))
                    nid = note["note_id"]
                    if not nid or nid in seen:
                        continue
                    if min_interaction and note["interaction_score"] < min_interaction:
                        continue
                    seen[nid] = note
                    added += 1
                    if len(seen) >= target:
                        break
                empty_streak = empty_streak + 1 if added == 0 else 0
                if empty_streak >= 2:  # 连续两页无新增 → 该关键词到底，换下一个
                    break
                page += 1

    notes = sorted(seen.values(), key=lambda n: n.get("interaction_score", 0), reverse=True)[:target]
    crawled_count = len(seen) - cached_count

    # 3) 可选：批量补全 top 笔记正文/标签
    detail_note = 0
    if with_detail and notes:
        top_ids = [n["note_id"] for n in notes[:_MAX_DETAIL_IN_COLLECT]]
        details, d_units = await _fetch_details_for(top_ids)
        units += d_units
        for n in notes:
            d = details.get(n["note_id"])
            if d:
                n["desc"] = d["desc"] or n["desc"]
                n["tags"] = d["tags"] or n["tags"]
                detail_note += 1

    bb = get_blackboard(ctx)
    handle = bb.put_dataset(
        "notes",
        notes,
        meta={"keywords": keywords, "target": target, "cached": cached_count, "crawled": crawled_count, "sort": sort},
    )
    summary = (
        f"已为关键词 {keywords} 汇集 {len(notes)} 条去重笔记"
        f"（缓存命中 {cached_count} + 实时补采 {crawled_count}；目标 {target}"
        + (f"；互动量≥{min_interaction}" if min_interaction else "")
        + (f"；已补全 {detail_note} 条正文" if with_detail else "")
        + ")。"
    )
    logger.info("[tool.collect_notes] {} units={}", summary, units)
    extra_hint = (
        f'后续用 query_dataset(handle="{handle}") 取子集分析，'
        "或 fetch_notes_details 补全更多正文，或 present_artifact 生成结构化产物。"
    )
    return ToolResult(
        ok=True,
        content=_preview_block(notes, handle, summary, extra_hint=extra_hint),
        data={"handle": handle, "count": len(notes), "cached": cached_count, "crawled": crawled_count, "keywords": keywords},
        display=summary.split("。")[0],
        units=max(units, 0),
    )


async def _fetch_notes_details(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    raw_ids = args.get("note_ids")
    note_ids: List[str] = []
    if isinstance(raw_ids, list):
        note_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    elif isinstance(raw_ids, str) and raw_ids.strip():
        note_ids = [s.strip() for s in re.split(r"[,\s]+", raw_ids) if s.strip()]
    if not note_ids:
        return ToolResult(ok=False, content="fetch_notes_details 需要 note_ids（数组）", error="MISSING_ARGS")
    note_ids = note_ids[:_MAX_DETAIL_IN_COLLECT]

    details, units = await _fetch_details_for(note_ids)
    rows = [{"note_id": nid, "desc": d["desc"], "tags": d["tags"]} for nid, d in details.items()]
    bb = get_blackboard(ctx)
    handle = bb.put_dataset("details", rows, meta={"requested": len(note_ids), "got": len(rows)})
    summary = f"批量补全详情：请求 {len(note_ids)} 条，成功 {len(rows)} 条。已存入 handle={handle}。"
    logger.info("[tool.fetch_notes_details] {} units={}", summary, units)
    # 正文可能较长：回注每条前 300 字预览，全文在黑板
    preview = [{"note_id": r["note_id"], "desc": (r["desc"] or "")[:300], "tags": r["tags"]} for r in rows[:PREVIEW_DEFAULT]]
    content = (
        summary + "\n字段：note_id, desc(全文), tags\n"
        f"预览（前 {len(preview)} 条，正文截断 300 字）：\n{json.dumps(preview, ensure_ascii=False)}\n"
        f'取全文用 query_dataset(handle="{handle}")。'
    )
    return ToolResult(ok=True, content=content, data={"handle": handle, "rows": rows}, display=summary.split("。")[0], units=max(units, 1))


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="search_notes",
            description="按关键词搜索小红书笔记（单页），返回标题/正文摘要/互动量/发布时间/链接等。结果存入工作记忆并返回句柄。需要大量笔记时优先用 collect_notes。",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如『Fazer 巧克力』"},
                    "page": {"type": "integer", "minimum": 1, "description": "页码，从 1 开始"},
                    "sort": {"type": "string", "enum": ["general", "latest"], "description": "排序：general 综合 / latest 最新"},
                    "note_type": {"type": "string", "enum": ["all", "video", "image"], "description": "笔记类型"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "本页最多返回多少条，默认 20"},
                },
                "required": ["keyword"],
            },
            handler=_search_notes,
            cost="expensive",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="collect_notes",
            description=(
                "【批量】按目标数量采集某关键词（可多关键词）的小红书笔记：优先复用数据库缓存，不足部分实时翻页补采，"
                "自动按 note_id 去重并按互动量排序。完成后存入工作记忆并返回句柄。"
                "这是『检索某品牌/品类近期 N 篇笔记并逐条拆解』类任务的首选入口，比反复 search_notes 更省。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5, "description": "搜索关键词（1~5 个）"},
                    "target_count": {"type": "integer", "minimum": 1, "maximum": _TARGET_HARD_CAP, "description": "目标采集数量，默认取界面设置或 50"},
                    "sort": {"type": "string", "enum": ["general", "latest"], "description": "排序：general 综合 / latest 最新"},
                    "note_type": {"type": "string", "enum": ["all", "video", "image"], "description": "笔记类型"},
                    "min_interaction": {"type": "integer", "minimum": 0, "description": "互动量（点赞+收藏+评论）下限，低于此值的笔记丢弃"},
                    "with_detail": {"type": "boolean", "description": "是否顺带批量补全 top 笔记的完整正文与话题标签（更慢更贵），默认 false"},
                },
                "required": ["keywords"],
            },
            handler=_collect_notes,
            cost="expensive",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_note_detail",
            description="获取单条笔记的完整正文（不截断）与话题标签。补全某一条关键笔记的全文时用。批量补全请用 fetch_notes_details。",
            parameters={
                "type": "object",
                "properties": {"note_id": {"type": "string", "description": "笔记 ID（来自搜索/采集结果）"}},
                "required": ["note_id"],
            },
            handler=_fetch_note_detail,
            cost="expensive",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_notes_details",
            description="【批量】一次补全多条笔记的完整正文与话题标签（最多 15 条）。结果存入工作记忆并返回句柄。逐条拆解前用它批量补全正文，比多次 fetch_note_detail 省调用。",
            parameters={
                "type": "object",
                "properties": {
                    "note_ids": {"type": "array", "items": {"type": "string"}, "description": "笔记 ID 列表（最多 15 条）"},
                },
                "required": ["note_ids"],
            },
            handler=_fetch_notes_details,
            cost="expensive",
            category="capability",
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_comments",
            description="采集单条笔记评论区的评论（含点赞数/作者/IP 属地），用于评论洞察。结果存入工作记忆并返回句柄。可设 max_comments / max_pages 控制规模。",
            parameters={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "笔记 ID"},
                    "max_comments": {"type": "integer", "minimum": 1, "maximum": 200, "description": "最多采集多少条评论，默认 50"},
                    "max_pages": {"type": "integer", "minimum": 1, "maximum": 15, "description": "最多翻多少页，默认 2（需要更全量评论时再调大）"},
                },
                "required": ["note_id"],
            },
            handler=_fetch_comments,
            cost="expensive",
            category="capability",
        )
    )
