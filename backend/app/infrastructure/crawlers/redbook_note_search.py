"""共用的第三方 Redbook API 按关键词分页搜索逻辑。

从 comment_pipeline._step1_crawl_notes 抽取，供评论分析流水线与爆文分析
CrawlerAgent（第三方采集分支）共用。只负责与业务无关的采集循环：

    按关键词逐个翻页 → note_to_dict → 可选 detail 补全（全文 desc + 话题标签）
    → 可选 tier2_filter → 去重（note_id 全局去重）→ 达到目标条数即停

`sort` 参数原样透传给 `RedbookApiClient.search()`，本函数不关心具体取值
（评论流水线传 "time_descending"，爆文侧传 "popularity_descending"）。
排序（如"含型号词优先"、按互动量重排等）以及评论采集均由调用方在拿到
返回结果后自行处理，不在本函数职责范围内。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger

from .redbook_api_client import RedbookApiClient

_MAX_PAGES_PER_KW = 30           # 每关键词最多请求页数，避免无限循环（每页约18条笔记）
_MAX_CONSECUTIVE_EMPTY_PAGES = 3  # 连续 N 页搜索返回 0 条才终止翻页
_MAX_CONSECUTIVE_DUP_PAGES = 2    # 连续 N 页全部重复才终止翻页

LogHook = Callable[[str], Awaitable[None]]
Tier2Filter = Callable[[Dict[str, Any]], Awaitable[bool]]


async def search_notes_for_keywords(
    keywords: List[str],
    *,
    sort: str,
    per_keyword_target: int,
    fetch_detail: bool = True,
    exclude_note_ids: Optional[Set[str]] = None,
    tier2_filter: Optional[Tier2Filter] = None,
    log_hook: Optional[LogHook] = None,
    inter_page_sleep: Tuple[float, float] = (0.5, 1.5),
    inter_detail_sleep: Tuple[float, float] = (3.0, 4.0),
) -> List[Dict[str, Any]]:
    """按关键词逐个分页搜索三方 API，返回全局去重后的 note dict 列表（不排序）。

    Args:
        keywords: 搜索关键词列表，逐个串行采集。
        sort: 透传给 `RedbookApiClient.search()` 的排序方式。
        per_keyword_target: 每个关键词的目标采集条数。
        fetch_detail: 是否对每条新笔记调用 `get_detail` 补全全文 desc + 话题标签。
        exclude_note_ids: 已在库中的 note_id 集合，命中时跳过（补爬去重场景）。
        tier2_filter: 逐条笔记的异步精判 hook，返回 False 时丢弃该笔记。
        log_hook: 逐关键词/逐页进展的异步日志回调（用于 SSE 播报）；不传则仅走 logger。
        inter_page_sleep: 翻页之间的随机 sleep 区间（秒）。
        inter_detail_sleep: 相邻两次 detail 补全请求之间的随机 sleep 区间（秒）。
    """
    client = RedbookApiClient()
    all_notes: Dict[str, Dict[str, Any]] = {}
    total_kw = len(keywords)

    async def _log(msg: str) -> None:
        if log_hook:
            await log_hook(msg)

    for kw_idx, keyword in enumerate(keywords, start=1):
        collected_for_kw = 0
        page = 1
        prev_page_first_id = ""
        consecutive_empty_pages = 0
        consecutive_dup_pages = 0
        logger.info(
            f"[SharedSearch] ▶ 开始采集关键词「{keyword}」"
            f"  目标={per_keyword_target} 条  sort={sort}  最大页数={_MAX_PAGES_PER_KW}"
        )
        await _log(f"开始采集关键词「{keyword}」（{kw_idx}/{total_kw}），目标 {per_keyword_target} 条")

        while collected_for_kw < per_keyword_target and page <= _MAX_PAGES_PER_KW:
            try:
                # 网络重试由 client.search 内部负责；0 条结果不在本页重试，改为跳过继续翻页
                raw_resp = await asyncio.to_thread(client.search, keyword, page, sort)
            except Exception as exc:
                logger.warning(
                    f"[SharedSearch] 「{keyword}」page={page} 搜索失败（已内部重试），终止本关键词: {exc}"
                )
                break

            raw_notes = RedbookApiClient.parse_search_notes(raw_resp)
            if not raw_notes:
                consecutive_empty_pages += 1
                logger.warning(
                    f"[SharedSearch] 「{keyword}」page={page}  本页 0 条"
                    f"（连续空页 {consecutive_empty_pages}/{_MAX_CONSECUTIVE_EMPTY_PAGES}），"
                    f"跳过本页继续翻页"
                )
                if consecutive_empty_pages >= _MAX_CONSECUTIVE_EMPTY_PAGES:
                    logger.info(
                        f"[SharedSearch] 「{keyword}」连续 {_MAX_CONSECUTIVE_EMPTY_PAGES} 页无结果，终止翻页"
                    )
                    break
                page += 1
                if page <= _MAX_PAGES_PER_KW and collected_for_kw < per_keyword_target:
                    await asyncio.sleep(random.uniform(*inter_page_sleep))
                continue

            consecutive_empty_pages = 0

            page_ids = [str(n.get("id") or "").strip() for n in raw_notes if n.get("id")]
            if page_ids:
                if prev_page_first_id and page_ids[0] == prev_page_first_id:
                    logger.warning(
                        f"[SharedSearch] 「{keyword}」page={page}  "
                        f"首条 note_id 与上一页相同（{page_ids[0]}），分页可能抖动，继续翻页"
                    )
                prev_page_first_id = page_ids[0]

            page_deduped = page_detail_ok = page_detail_fail = page_t2_reject = page_added = 0
            for raw_note in raw_notes:
                note_dict = RedbookApiClient.note_to_dict(raw_note, keyword)
                nid = note_dict.get("note_id")
                if not nid or nid in all_notes or (exclude_note_ids and nid in exclude_note_ids):
                    page_deduped += 1
                    continue

                if fetch_detail:
                    try:
                        detail_resp = await asyncio.to_thread(client.get_detail, nid)
                        detail_note = RedbookApiClient.parse_detail_note(detail_resp)
                        if detail_note:
                            enriched = RedbookApiClient.extract_detail_fields(detail_note)
                            note_dict["desc"] = enriched["desc"]
                            note_dict["tags"] = enriched["tags"]
                            page_detail_ok += 1
                        else:
                            page_detail_fail += 1
                    except Exception as exc:
                        page_detail_fail += 1
                        logger.debug(
                            f"[SharedSearch] detail 补全失败 note_id={nid}，保留截断摘要: {exc}"
                        )
                    # detail 请求间随机间隔，避免连续请求触发服务端速率限制
                    await asyncio.sleep(random.uniform(*inter_detail_sleep))

                if tier2_filter is not None:
                    try:
                        passed = await tier2_filter(note_dict)
                        if not passed:
                            page_t2_reject += 1
                            continue
                    except Exception:
                        pass

                all_notes[nid] = note_dict
                collected_for_kw += 1
                page_added += 1
                if collected_for_kw >= per_keyword_target:
                    break

            logger.info(
                f"[SharedSearch] 「{keyword}」page={page}  本页结果: "
                f"新增={page_added}  去重跳过={page_deduped}  "
                f"detail成功={page_detail_ok} 失败={page_detail_fail}  "
                f"Tier2淘汰={page_t2_reject}  "
                f"累计={collected_for_kw}/{per_keyword_target}（全局={len(all_notes)}）"
            )
            await _log(
                f"「{keyword}」第 {page} 页：新增 {page_added} 条，"
                f"累计 {collected_for_kw}/{per_keyword_target}（全局 {len(all_notes)} 条）"
            )

            # 本页全部 note_id 均已入库 → 可能是翻页抖动，连续多页才终止
            if len(raw_notes) > 0 and page_added == 0 and page_deduped == len(raw_notes):
                consecutive_dup_pages += 1
                logger.warning(
                    f"[SharedSearch] 「{keyword}」page={page}  "
                    f"本页 {len(raw_notes)} 条全部重复"
                    f"（连续重复页 {consecutive_dup_pages}/{_MAX_CONSECUTIVE_DUP_PAGES}），继续翻页"
                )
                if consecutive_dup_pages >= _MAX_CONSECUTIVE_DUP_PAGES:
                    logger.warning(
                        f"[SharedSearch] 「{keyword}」连续 {_MAX_CONSECUTIVE_DUP_PAGES} 页全部重复，终止翻页"
                    )
                    break
            else:
                consecutive_dup_pages = 0

            if collected_for_kw >= per_keyword_target:
                logger.info(f"[SharedSearch] 「{keyword}」已达目标 {per_keyword_target} 条，停止翻页")
                break

            page += 1
            if page <= _MAX_PAGES_PER_KW and collected_for_kw < per_keyword_target:
                await asyncio.sleep(random.uniform(*inter_page_sleep))

        logger.info(
            f"[SharedSearch] ◀ 关键词「{keyword}」采集完毕  "
            f"本轮新增={collected_for_kw}  全局总计={len(all_notes)}"
        )
        await _log(f"关键词「{keyword}」采集完毕，本轮新增 {collected_for_kw} 条（全局累计 {len(all_notes)} 条）")

    return list(all_notes.values())
