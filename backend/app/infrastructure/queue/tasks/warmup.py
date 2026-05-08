"""
定时预热 & 按需预热任务（阶段 4.α-patch2）。

`scheduled_warmup(ctx, force=False)` —— 同一入口,两种触发来源：
- **ARQ cron 触发**（每小时探针,force=False）：
  - 门控 1: `crawler_schedule.enabled = False` → skip
  - 门控 2: 距上次 finished_at 不到 `interval_hours` → skip
  - 以上都过再真跑。这样用户前端改 `interval_hours` 立即生效,无需重启 worker
- **用户"立即采集"触发**（API 层 enqueue 时 force=True）：
  - 门控 1: 开关关着仍然 skip（尊重管理员总开关）
  - 门控 2: **跳过**(穿透 interval,立即执行)
  - 这样用户连续点"立即采集"都能跑,不会被 interval 节流拦截

核心任务流程：
- 合并 focus_keywords ∪ Redis 热词 Top N → 去重
- 逐个跑 CrawlerAgent 的核心采集函数 → 写 L1 Redis + L2 pgvector
- 每个关键词间隔 30s,避免触发 XHS 反爬
- 结果写 Redis `crawler:last_run`（供设置页 `/crawler-status` 显示）

`warmup_keyword_on_demand`（带关键词的按需预热,保留给其他场景）：
- 不走焦点关键词列表,而是按调用方传入的关键词列表跑
- 不受开关/interval 限制(调用方自己决策)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


_DEFAULT_MAX_KEYWORDS = 30  # hot_keywords_top_n 未配置时的兜底
_INTER_KEYWORD_SLEEP_SEC = 30.0
_PER_KEYWORD_TARGET = 30


async def scheduled_warmup(ctx: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """定时 / 立即预热任务（共用入口）。

    Args:
        ctx: ARQ 任务上下文
        force: True=用户"立即采集"触发,跳过 interval 节流(但仍受开关限制);
               False=cron 探针触发,受双门控限制(开关 + interval)

    门控规则：
    - 开关(enabled) = False: 无论 force 都 skip
    - force=False 且距上次执行不到 interval_hours: skip
    - 其他情况: 执行采集
    """
    trigger = "manual" if force else "scheduled"
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.scheduled_warmup] 开始,trigger={trigger} time={start.isoformat()}")

    try:
        from ....services.system_settings_store import get_system_settings_store

        schedule_cfg = await get_system_settings_store().get_crawler_schedule()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.scheduled_warmup] 读取设置失败({exc}),按默认启用处理")
        schedule_cfg = {"enabled": True, "interval_hours": 6, "hot_keywords_top_n": _DEFAULT_MAX_KEYWORDS}

    # ----- 门控 1: 开关 (无条件检查,force 也不能绕过) -----
    if not schedule_cfg.get("enabled", True):
        logger.info("[arq.scheduled_warmup] 定时预热已在设置页禁用,跳过本次")
        return {
            "status": "skipped",
            "reason": "admin_disabled",
            "trigger": trigger,
            "started_at": start.isoformat(),
        }

    # ----- 门控 2: interval (仅 force=False 时检查,立即采集穿透) -----
    interval_hours = int(schedule_cfg.get("interval_hours", 6))
    if not force:
        if not await _interval_reached(start, interval_hours):
            logger.info(
                f"[arq.scheduled_warmup] 距上次执行未到 {interval_hours}h 间隔,跳过本次 (cron 节流)"
            )
            return {
                "status": "skipped",
                "reason": "interval_not_reached",
                "trigger": trigger,
                "interval_hours": interval_hours,
            }
    else:
        logger.info(
            f"[arq.scheduled_warmup] 立即采集模式(force=True),穿透 interval={interval_hours}h 节流"
        )

    max_keywords = int(schedule_cfg.get("hot_keywords_top_n", _DEFAULT_MAX_KEYWORDS))
    logger.info(
        f"[arq.scheduled_warmup] 门控通过,top_n={max_keywords} interval_hours={interval_hours}"
    )

    try:
        keywords = await _collect_warmup_keywords()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.scheduled_warmup] 收集关键词失败: {exc}")
        keywords = []

    if not keywords:
        logger.info("[arq.scheduled_warmup] 无可预热关键词,跳过")
        await _write_last_run(
            {
                "status": "skipped",
                "reason": "no_keywords",
                "trigger": trigger,
                "started_at": start.isoformat(),
            }
        )
        return {"status": "skipped", "reason": "no_keywords", "trigger": trigger}

    keywords = keywords[:max_keywords]
    logger.info(f"[arq.scheduled_warmup] 预热 {len(keywords)} 个关键词: {keywords}")

    results = {"ok": 0, "failed": 0, "skipped": 0, "per_keyword": []}
    for idx, kw in enumerate(keywords):
        try:
            crawled = await _warmup_one_keyword(kw)
            if crawled:
                results["ok"] += 1
                results["per_keyword"].append({"keyword": kw, "count": crawled, "status": "ok"})
            else:
                results["skipped"] += 1
                results["per_keyword"].append({"keyword": kw, "count": 0, "status": "empty"})
        except Exception as exc:  # noqa: BLE001
            results["failed"] += 1
            results["per_keyword"].append({"keyword": kw, "status": "error", "error": str(exc)})
            logger.warning(f"[arq.scheduled_warmup] 预热 {kw} 失败: {exc}")

        if idx < len(keywords) - 1:
            await asyncio.sleep(_INTER_KEYWORD_SLEEP_SEC)

    end = datetime.now(timezone.utc)
    summary = {
        "status": "ok" if results["ok"] > 0 else "empty",
        "trigger": trigger,
        "started_at": start.isoformat(),
        "finished_at": end.isoformat(),
        "duration_sec": (end - start).total_seconds(),
        "total_keywords": len(keywords),
        "ok_count": results["ok"],
        "failed_count": results["failed"],
        "skipped_count": results["skipped"],
        "per_keyword": results["per_keyword"],
    }
    await _write_last_run(summary)
    logger.info(f"[arq.scheduled_warmup] 完成: trigger={trigger} ok={results['ok']} failed={results['failed']}")
    return summary


async def warmup_keyword_on_demand(
    ctx: Dict[str, Any],
    keywords: List[str],
    target_count: int = 30,
) -> Dict[str, Any]:
    """管理员"立即采集"按钮触发：为一组关键词预热。"""
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.warmup_on_demand] keywords={keywords} target={target_count}")

    crawled = 0
    try:
        crawled = await _warmup_one_keyword(" ".join(keywords), target=target_count)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.warmup_on_demand] 失败: {exc}")
        return {"status": "error", "error": str(exc)}

    end = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "keywords": keywords,
        "crawled_count": crawled,
        "duration_sec": (end - start).total_seconds(),
    }


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


async def _collect_warmup_keywords() -> List[str]:
    """合并 focus_keywords ∪ top queries,去重去空白。

    关键词来源优先级：
    1. `FocusKeywordsStore`(datas/config/focus_keywords.json):
       前端"设置 → 焦点关键词"写入的真实用户配置（本阶段主渠道)
    2. `viral_agent.config.knowledge_loader.load_focus_keywords()`(兼容老路径):
       若老项目部署有该函数,继续吸收
    3. Redis `hot:queries`：近期 Top 查询关键词（由 rebuild_hot_queries cron 填充）

    三路合并后大小写不敏感去重,保持 `focus` 在 `top_queries` 之前。
    """
    focus: List[str] = []

    # 主渠道: FocusKeywordsStore
    try:
        from ....services.focus_keywords_store import get_focus_keywords_store

        focus = list(await get_focus_keywords_store().get())
        if focus:
            logger.info(f"[warmup] 从 FocusKeywordsStore 读取 {len(focus)} 个关键词")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] FocusKeywordsStore 读取失败: {exc}")
        focus = []

    # 兼容：老 knowledge_loader（若存在）
    if not focus:
        try:
            from viral_agent.config.knowledge_loader import (  # type: ignore[attr-defined]
                load_focus_keywords,
            )

            legacy = list(load_focus_keywords() or [])
            if legacy:
                logger.info(f"[warmup] 从 legacy knowledge_loader 补充 {len(legacy)} 个关键词")
                focus = legacy
        except Exception:
            pass

    top_queries: List[str] = []
    try:
        top_queries = await _load_top_queries_from_redis()
    except Exception:
        top_queries = []

    # 合并 + trim + 大小写不敏感去重(保留先后顺序：focus 优先)
    seen_lower: set[str] = set()
    merged: List[str] = []
    for kw in list(focus) + list(top_queries):
        s = str(kw).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(s)
    return merged


async def _load_top_queries_from_redis() -> List[str]:
    """从 Redis `hot:queries` 拿 Top N 历史关键词。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("hot:queries")
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        if isinstance(data, dict) and "keywords" in data:
            return [str(x) for x in data["keywords"]]
    except Exception:  # noqa: BLE001
        pass
    return []


async def _warmup_one_keyword(keyword: str, target: int = _PER_KEYWORD_TARGET) -> int:
    """为单个关键词跑一次 L3 → 写回 L1/L2,返回真实采到的条数。

    注意：此处调用 CrawlerAgent 的私有采集函数,不走完整编排（不产出画布）。
    阶段 4.α 为简化起见直接用 viral_collector,不走 ModelGateway。
    """
    try:
        from ....application.agents.crawler_agent import (
            _collect_one_dimension,
            _normalize_note,
            _resolve_cookies_str,
        )
        from ...cache.keyword_cache import get_keyword_cache
        from ...storage.notes_vector_store import get_notes_vector_store
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 依赖加载失败: {exc}")
        return 0

    cookies_str = _resolve_cookies_str("")  # 默认 admin
    if not cookies_str:
        logger.warning("[warmup] 未找到可用 cookies,跳过")
        return 0

    runtime_cfg = {
        "target_count": target,
        "viral_ratio": 0.5,
        "note_type": 0,
        "time_range": 0,
    }
    try:
        viral_notes = await _collect_one_dimension(
            cookies_str, [keyword], target, runtime_cfg
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 采集 {keyword} 失败: {exc}")
        return 0

    if not viral_notes:
        return 0

    normalized = [_normalize_note(n, "warmup", keyword) for n in viral_notes]
    # 回填 L1 Redis
    try:
        await get_keyword_cache().set([keyword], normalized)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] L1 回填失败: {exc}")
    # 回填 L2 pgvector
    try:
        await get_notes_vector_store().add_notes(normalized)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] L2 回填失败: {exc}")

    return len(normalized)


async def _write_last_run(summary: Dict[str, Any]) -> None:
    """写最近一次预热结果到 Redis,供设置页读取。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex(
            "crawler:last_run",
            86400,  # 保留 1 天
            json.dumps(summary, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[warmup._write_last_run] 写 Redis 失败: {exc}")


async def read_last_run() -> Optional[Dict[str, Any]]:
    """读最近一次预热结果（供 `/settings/crawler-status` 用）。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:last_run")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


async def _interval_reached(now: datetime, interval_hours: int) -> bool:
    """距上次实际执行是否已满 `interval_hours`(仅 cron 触发用,force=True 跳过)。

    判定规则：
    - 读 Redis `crawler:last_run.finished_at`(ISO 字符串)
    - 解析失败 / 读不到 / 不是真实执行过的状态 → 视为可执行(True)
    - 未到间隔返回 False

    注意：Redis 不可用时返回 True,让本次尝试执行（避免因基础设施故障导致永远跳过）。
    """
    if interval_hours <= 0:
        return True

    last_run = await read_last_run()
    if not last_run:
        return True

    # 只有上次真实执行过(ok / empty / no_keywords)才计入间隔,其他 skip 状态不算
    last_status = str(last_run.get("status") or "")
    if last_status not in {"ok", "empty", "no_keywords"}:
        return True

    finished_raw = last_run.get("finished_at") or last_run.get("started_at")
    if not finished_raw:
        return True

    try:
        last_ts = datetime.fromisoformat(str(finished_raw).replace("Z", "+00:00"))
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return True

    elapsed_sec = (now - last_ts).total_seconds()
    required_sec = interval_hours * 3600
    # 留 1 分钟容差,避免 cron 抖动刚好少 1 秒不跑
    return elapsed_sec + 60 >= required_sec
