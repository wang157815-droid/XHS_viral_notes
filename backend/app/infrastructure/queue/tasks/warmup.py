"""
定时预热 & 按需预热任务。

`scheduled_warmup(ctx, force=False)` —— 同一入口,两种触发来源：
- **ARQ cron 触发**（每小时探针,force=False）：
  - 门控 1: `crawler_schedule.enabled = False` → skip
  - 门控 2: 当前时间未到 `crawler:next_run_at`（Redis）→ skip
  - 调度策略：每次执行完后随机生成 12~24h 后的下次执行时间，
    保证每天至少执行一次且时间点随机（防规律性访问）
- **用户"立即采集"触发**（API 层 enqueue 时 force=True）：
  - 门控 1: 开关关着仍然 skip（尊重管理员总开关）
  - 门控 2: **跳过**（穿透时间节流,立即执行）
  - 执行完后同样重新抽签写入下次时间

核心任务流程：
- 合并 focus_keywords ∪ Redis 热词 Top N → 去重
- 逐个跑 CrawlerAgent 的核心采集函数 → 写 L1 Redis + L2 pgvector
- 每个关键词间隔 30s,避免触发 XHS 反爬
- 结果写 Redis `crawler:last_run`（供设置页 `/crawler-status` 显示）
- 写 Redis `crawler:next_run_at`（下次随机执行时间）

`warmup_keyword_on_demand`（带关键词的按需预热,保留给其他场景）：
- 不走焦点关键词列表,而是按调用方传入的关键词列表跑
- 不受开关/interval 限制(调用方自己决策)
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


_DEFAULT_MAX_KEYWORDS = 30  # hot_keywords_top_n 未配置时的兜底
_INTER_KEYWORD_SLEEP_SEC = 30.0
_PER_KEYWORD_TARGET = 50  # 与前端 DEFAULT_ADVANCED sample_count="50" 对齐


async def scheduled_warmup(ctx: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """定时 / 立即预热任务（共用入口）。

    Args:
        ctx: ARQ 任务上下文
        force: True=用户"立即采集"触发,跳过时间节流(但仍受开关限制);
               False=cron 探针触发,受双门控限制(开关 + next_run_at)

    门控规则：
    - 开关(enabled) = False: 无论 force 都 skip
    - force=False 且当前时间 < crawler:next_run_at: skip
    - 其他情况: 执行采集，完成后随机生成 12~24h 后的下次时间
    """
    trigger = "manual" if force else "scheduled"
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.scheduled_warmup] 开始,trigger={trigger} time={start.isoformat()}")

    try:
        from ....services.system_settings_store import get_system_settings_store

        schedule_cfg = await get_system_settings_store().get_crawler_schedule()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.scheduled_warmup] 读取设置失败({exc}),按默认启用处理")
        schedule_cfg = {"enabled": True}

    # ----- 门控 1: 开关 (无条件检查,force 也不能绕过) -----
    if not schedule_cfg.get("enabled", True):
        logger.info("[arq.scheduled_warmup] 定时预热已在设置页禁用,跳过本次")
        return {
            "status": "skipped",
            "reason": "admin_disabled",
            "trigger": trigger,
            "started_at": start.isoformat(),
        }

    # ----- 门控 2: next_run_at (仅 force=False 时检查,立即采集穿透) -----
    if not force:
        if not await _next_run_at_reached(start):
            logger.info("[arq.scheduled_warmup] 未到计划执行时间，跳过本次 (cron 节流)")
            return {
                "status": "skipped",
                "reason": "not_yet",
                "trigger": trigger,
                "started_at": start.isoformat(),
            }
    else:
        logger.info("[arq.scheduled_warmup] 立即采集模式(force=True),穿透时间节流")

    max_keywords = _DEFAULT_MAX_KEYWORDS
    logger.info(f"[arq.scheduled_warmup] 门控通过,top_n={max_keywords}")

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
    await _write_next_run_at(end)
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


def _find_warmup_user_id() -> str:
    """从 XhsCredentialStore 找第一个已绑定 XHS 账号的 RedMuse 用户 ID。

    优先取 admin 角色用户，其次取任意已绑定用户。
    返回空字符串表示没有任何可用凭据（此时走 .env COOKIES 兜底）。
    """
    try:
        from ....services.xhs_auth import get_credential_store
        from ....services.identity_store import get_identity_store

        credentials = get_credential_store().list_credentials()
        if not credentials:
            return ""

        # 收集有效凭据的 redmuse_user_id
        valid_ids = [
            c.redmuse_user_id for c in credentials
            if c.redmuse_user_id and c.cookies_path and c.status != "unbound"
        ]
        if not valid_ids:
            return ""

        # 优先选 admin 角色
        store = get_identity_store()
        for uid in valid_ids:
            record = store.get(uid)
            if record and str(record.get("role", "")).lower() == "admin":
                logger.info(f"[warmup] 使用 admin 用户凭据: {uid}")
                return uid

        # 无 admin 则取第一个有效用户
        logger.info(f"[warmup] 无 admin 凭据，使用第一个可用用户: {valid_ids[0]}")
        return valid_ids[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 查找用户凭据失败: {exc}")
        return ""


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

    # 优先使用 XhsCredentialStore 中绑定的账号（管理员优先）
    owner_user_id = _find_warmup_user_id()
    cookies_str = _resolve_cookies_str(owner_user_id)
    if not cookies_str:
        logger.warning("[warmup] 未找到可用 cookies（请在设置→数据源授权中绑定小红书账号）,跳过")
        return 0

    runtime_cfg = {
        "target_count": target,
        "viral_ratio": 0.5,
        "note_type": 0,
        "time_range": 0,
        "min_interaction": 0,
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


async def _next_run_at_reached(now: datetime) -> bool:
    """读取 Redis crawler:next_run_at，判断是否到了执行时间。

    - 不存在（首次部署）→ True（立即执行）
    - now >= next_run_at  → True
    - now < next_run_at   → False
    - Redis 故障          → True（放行，避免永不执行）
    """
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:next_run_at")
        if not raw:
            return True
        next_run = datetime.fromisoformat(raw.decode().replace("Z", "+00:00"))
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        if now >= next_run:
            return True
        remaining_h = (next_run - now).total_seconds() / 3600
        logger.info(f"[warmup] 距下次执行还有 {remaining_h:.1f}h（{next_run.isoformat()}）")
        return False
    except Exception:  # noqa: BLE001
        return True


async def _write_next_run_at(now: datetime) -> None:
    """随机生成 12~24h 后的时间戳写入 Redis，供下次探针判定。"""
    offset_sec = random.uniform(12 * 3600, 24 * 3600)
    next_run = now + timedelta(seconds=offset_sec)
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex("crawler:next_run_at", 86400 * 2, next_run.isoformat())
        logger.info(f"[warmup] 下次计划执行: {next_run.isoformat()} (距今 {offset_sec/3600:.1f}h)")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 写入 next_run_at 失败: {exc}")


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


async def read_next_run_at() -> Optional[str]:
    """读取下次计划执行时间（ISO 字符串），供 `/settings/crawler-status` 用。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:next_run_at")
        if not raw:
            return None
        return raw.decode()
    except Exception:  # noqa: BLE001
        return None
