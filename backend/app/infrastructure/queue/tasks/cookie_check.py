"""
Cookie 健康巡检任务（阶段 4.α）。

cron 每 4 小时跑一次,结果写 Redis 供管理员页面查询。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger


async def cookie_health_check(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ cron：Cookie 健康巡检。"""
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.cookie_health_check] 开始,时间={start.isoformat()}")

    try:
        from ....services.cookie_health_service import cookie_health_service
        # force=True 绕过 300s 缓存,拿真实状态
        status = cookie_health_service.get_cookie_health(force_check=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.cookie_health_check] 失败: {exc}")
        return {"status": "error", "error": str(exc)}

    end = datetime.now(timezone.utc)
    summary = {
        "checked_at": start.isoformat(),
        "finished_at": end.isoformat(),
        "duration_ms": int((end - start).total_seconds() * 1000),
        "status": status.get("status", "unknown"),
        "message": status.get("message", ""),
        "saved_days": status.get("saved_days", 0),
    }

    # 持久化最近一次巡检结果
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex("cookie:last_check", 86400, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[cookie_health_check] 写 Redis 失败: {exc}")

    logger.info(f"[arq.cookie_health_check] 结果: {summary['status']}")
    return summary


async def read_last_cookie_check() -> Optional[Dict[str, Any]]:
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("cookie:last_check")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
