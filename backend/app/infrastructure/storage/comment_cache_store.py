"""
评论爬取缓存存储（PostgreSQL 表 comment_crawl_cache）。

职责：
- 每次爬完笔记+评论后，按 (keywords, time_range, min_interaction) 写入缓存
- 下次同样参数发起任务时，先查缓存；命中则直接复用，跳过爬取
- 缓存默认有效期 COMMENT_CACHE_TTL_DAYS 天（默认 7 天）

降级策略：
- PostgreSQL 不可用时静默降级（cache_miss），流水线照常爬取
- 写缓存失败时只打 WARNING，不中断流水线

env：
- COMMENT_CACHE_TTL_DAYS   缓存有效天数，默认 7
- COMMENT_CACHE_ENABLED    false 可完全关闭缓存，默认 true
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from sqlalchemy import text as sql_text
    _SA_AVAILABLE = True
except ImportError:
    sql_text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False

from .db_engine import PostgresUnavailable, get_db_session, is_postgres_available

_TTL_DAYS = int(os.getenv("COMMENT_CACHE_TTL_DAYS", "7"))
_ENABLED = os.getenv("COMMENT_CACHE_ENABLED", "true").lower() not in ("false", "0", "no")

_INIT_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS comment_crawl_cache (
        id              BIGSERIAL PRIMARY KEY,
        cache_key       VARCHAR(64)  NOT NULL UNIQUE,
        keywords        JSONB        NOT NULL,
        time_range      INTEGER      NOT NULL DEFAULT 0,
        min_interaction INTEGER      NOT NULL DEFAULT 0,
        notes_json      TEXT         NOT NULL,
        note_count      INTEGER      NOT NULL DEFAULT 0,
        comment_count   INTEGER      NOT NULL DEFAULT 0,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        expires_at      TIMESTAMPTZ  NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_comment_cache_key     ON comment_crawl_cache (cache_key)",
    "CREATE INDEX IF NOT EXISTS idx_comment_cache_expires ON comment_crawl_cache (expires_at)",
]

_initialized = False


def _make_cache_key(keywords: List[str], time_range: int, min_interaction: int) -> str:
    """用关键词（排序后）+ 时间范围 + 互动量下限 生成 SHA256 缓存键。"""
    payload = json.dumps(
        {"kw": sorted(k.strip().lower() for k in keywords), "tr": time_range, "mi": min_interaction},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


async def _ensure_table() -> None:
    global _initialized
    if _initialized:
        return
    if not _SA_AVAILABLE or not is_postgres_available():
        return
    try:
        async with get_db_session() as session:
            for stmt in _INIT_STATEMENTS:
                await session.execute(sql_text(stmt))
        _initialized = True
        logger.info("[comment_cache] comment_crawl_cache 表已就绪（首次建表或已存在）")
    except Exception as exc:
        logger.warning(f"[comment_cache] 建表失败（将降级跳过缓存）: {exc}")


class CommentCrawlCacheStore:
    """评论爬取缓存 CRUD。"""

    async def setup(self) -> None:
        """在应用启动时调用（幂等建表）。"""
        await _ensure_table()

    async def get(
        self,
        keywords: List[str],
        time_range: int = 0,
        min_interaction: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """查询缓存。命中返回 {"notes": [...], "note_count": N, "comment_count": M}；未命中返回 None。"""
        if not _ENABLED:
            return None
        if not _SA_AVAILABLE or not is_postgres_available():
            return None

        await _ensure_table()
        cache_key = _make_cache_key(keywords, time_range, min_interaction)
        now = datetime.now(timezone.utc)

        try:
            async with get_db_session() as session:
                row = (await session.execute(
                    sql_text(
                        "SELECT notes_json, note_count, comment_count, created_at "
                        "FROM comment_crawl_cache "
                        "WHERE cache_key = :key AND expires_at > :now "
                        "LIMIT 1"
                    ),
                    {"key": cache_key, "now": now},
                )).fetchone()

            if not row:
                return None

            notes = json.loads(row.notes_json)
            age_h = int((now - row.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600)
            logger.info(
                f"[comment_cache] 命中 key={cache_key[:8]}... "
                f"notes={row.note_count} comments={row.comment_count} age={age_h}h"
            )
            return {
                "notes": notes,
                "note_count": row.note_count,
                "comment_count": row.comment_count,
                "from_cache": True,
                "cache_age_hours": age_h,
            }
        except (PostgresUnavailable, Exception) as exc:
            logger.warning(f"[comment_cache] 读取失败（降级跳过）: {exc}")
            return None

    async def set(
        self,
        keywords: List[str],
        time_range: int,
        min_interaction: int,
        notes: List[Dict[str, Any]],
        comment_count: int,
    ) -> None:
        """写入/覆盖缓存。失败时静默降级。"""
        if not _ENABLED:
            return
        if not _SA_AVAILABLE or not is_postgres_available():
            return

        await _ensure_table()
        cache_key = _make_cache_key(keywords, time_range, min_interaction)
        expires_at = datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS)

        try:
            notes_json = json.dumps(notes, ensure_ascii=False)
            async with get_db_session() as session:
                await session.execute(
                    sql_text("""
                        INSERT INTO comment_crawl_cache
                            (cache_key, keywords, time_range, min_interaction,
                             notes_json, note_count, comment_count, expires_at)
                        VALUES
                            (:key, CAST(:kw AS JSONB), :tr, :mi,
                             :notes_json, :nc, :cc, :exp)
                        ON CONFLICT (cache_key) DO UPDATE SET
                            notes_json      = EXCLUDED.notes_json,
                            note_count      = EXCLUDED.note_count,
                            comment_count   = EXCLUDED.comment_count,
                            created_at      = NOW(),
                            expires_at      = EXCLUDED.expires_at
                    """),
                    {
                        "key": cache_key,
                        "kw": json.dumps(sorted(keywords), ensure_ascii=False),
                        "tr": time_range,
                        "mi": min_interaction,
                        "notes_json": notes_json,
                        "nc": len(notes),
                        "cc": comment_count,
                        "exp": expires_at,
                    },
                )
            logger.info(
                f"[comment_cache] 写入 key={cache_key[:8]}... "
                f"notes={len(notes)} comments={comment_count} ttl={_TTL_DAYS}d"
            )
        except (PostgresUnavailable, Exception) as exc:
            logger.warning(f"[comment_cache] 写入失败（忽略）: {exc}")

    async def delete_expired(self) -> int:
        """清理过期缓存，返回删除行数。"""
        if not _SA_AVAILABLE or not is_postgres_available():
            return 0
        try:
            async with get_db_session() as session:
                result = await session.execute(
                    sql_text("DELETE FROM comment_crawl_cache WHERE expires_at <= NOW()")
                )
                deleted = result.rowcount or 0
            if deleted:
                logger.info(f"[comment_cache] 清理过期缓存 {deleted} 条")
            return deleted
        except Exception as exc:
            logger.warning(f"[comment_cache] 清理失败: {exc}")
            return 0

    async def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息。"""
        if not _SA_AVAILABLE or not is_postgres_available():
            return {"enabled": False, "reason": "postgres_unavailable"}
        try:
            async with get_db_session() as session:
                row = (await session.execute(sql_text("""
                    SELECT
                        COUNT(*)                                    AS total,
                        COUNT(*) FILTER (WHERE expires_at > NOW()) AS valid,
                        COALESCE(SUM(note_count), 0)               AS notes,
                        COALESCE(SUM(comment_count), 0)            AS comments
                    FROM comment_crawl_cache
                """))).fetchone()
            return {
                "enabled": _ENABLED,
                "total": row.total,
                "valid": row.valid,
                "total_notes": row.notes,
                "total_comments": row.comments,
                "ttl_days": _TTL_DAYS,
            }
        except Exception as exc:
            return {"enabled": _ENABLED, "error": str(exc)}


comment_cache_store = CommentCrawlCacheStore()
