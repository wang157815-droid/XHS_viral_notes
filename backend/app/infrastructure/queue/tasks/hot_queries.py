"""
热词榜重建任务（阶段 4.α）。

cron 每 30 分钟跑：
- 从 TaskRepository 读过去 7 天的任务,统计 raw_input / keywords 中的 Top N 关键词
- 写 Redis `hot:queries` 供 scheduled_warmup 合并预热

简化处理：按 task 的 keywords 数组展开,统计频次（不做分词,因为 keywords 字段已经由
InputParserAgent 清洗过）。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from loguru import logger


_TOP_N = 20
_LOOKBACK_DAYS = 7


async def rebuild_hot_queries(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ cron：重建热词榜。"""
    started_at = datetime.now(timezone.utc)
    logger.info(f"[arq.rebuild_hot_queries] 开始")

    keywords = _collect_recent_keywords(lookback_days=_LOOKBACK_DAYS)
    if not keywords:
        logger.info("[arq.rebuild_hot_queries] 近期无任务,跳过")
        return {"status": "skipped", "reason": "no_recent_tasks"}

    counter = Counter(keywords)
    top = [kw for kw, _n in counter.most_common(_TOP_N)]

    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex(
            "hot:queries",
            86400 * 3,  # 保留 3 天,其实 30min 就会重建,更多是给容灾
            json.dumps(top, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.rebuild_hot_queries] 写 Redis 失败: {exc}")
        return {"status": "error", "error": str(exc)}

    finished_at = datetime.now(timezone.utc)
    summary = {
        "status": "ok",
        "top_keywords": top,
        "total_scanned": len(keywords),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
    }
    logger.info(f"[arq.rebuild_hot_queries] 完成: top {len(top)} 个")
    return summary


def _collect_recent_keywords(lookback_days: int) -> List[str]:
    """遍历 TaskRepository,把过去 N 天任务的 keywords 展开成 flat list。"""
    try:
        from ....infrastructure.repository import task_repository
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    flat: List[str] = []
    try:
        tasks = task_repository.list_all() if hasattr(task_repository, "list_all") else []
    except Exception:
        tasks = []

    for rec in tasks:
        # TaskRecord 可能是 dataclass 或 dict,做兼容读取
        created_at_raw = getattr(rec, "created_at", None) or (
            rec.get("created_at") if isinstance(rec, dict) else None
        )
        if not created_at_raw:
            continue
        try:
            if isinstance(created_at_raw, str):
                created = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            else:
                created = created_at_raw
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if created < cutoff:
            continue

        kws = getattr(rec, "keywords", None)
        if kws is None and isinstance(rec, dict):
            kws = rec.get("keywords")
        if isinstance(kws, list):
            flat.extend(str(k).strip() for k in kws if str(k).strip())

    return flat
