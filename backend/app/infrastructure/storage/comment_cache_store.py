"""
评论爬取缓存存储（PostgreSQL 两张规范化表）。

表结构
------
xhs_comment_note  ── 笔记表（一行一条笔记）
xhs_comment_data  ── 评论表（一行一条评论，父评论+子评论）

两表通过 cache_key 关联同一次搜索任务，通过 note_id 关联笔记与评论。

对外接口与旧版 comment_crawl_cache 完全兼容：
- get()  返回 {"notes": [...{...note_fields, "_cached_comments": [...]}], ...}
- set()  接收 notes=[{...note_fields, "_cached_comments": [...]}]
- delete_expired() / stats() 维持原签名

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
from collections import defaultdict
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

# ── DDL ──────────────────────────────────────────────────────────────────────

_INIT_STATEMENTS = [
    # ── 笔记表 ────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS xhs_comment_note (
        id                BIGSERIAL    PRIMARY KEY,
        cache_key         VARCHAR(64)  NOT NULL,
        note_id           VARCHAR(64)  NOT NULL,
        note_type         VARCHAR(32),
        title             TEXT,
        description       TEXT,
        note_url          TEXT,
        cover_url         TEXT,
        video_url         TEXT,
        likes             BIGINT       NOT NULL DEFAULT 0,
        comments          BIGINT       NOT NULL DEFAULT 0,
        collects          BIGINT       NOT NULL DEFAULT 0,
        share_count       BIGINT       NOT NULL DEFAULT 0,
        interaction_score BIGINT       NOT NULL DEFAULT 0,
        publish_time      TEXT,
        image_urls        TEXT,
        tag_list          TEXT,
        source_keyword    TEXT,
        xsec_token        TEXT,
        user_id           VARCHAR(255),
        nickname          TEXT,
        keywords          JSONB,
        time_range        INTEGER      NOT NULL DEFAULT 0,
        crawled_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        expires_at        TIMESTAMPTZ  NOT NULL,
        UNIQUE (cache_key, note_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_xcn_cache_exp ON xhs_comment_note (cache_key, expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_xcn_note_id   ON xhs_comment_note (note_id)",

    # ── 评论表 ────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS xhs_comment_data (
        id                BIGSERIAL    PRIMARY KEY,
        cache_key         VARCHAR(64)  NOT NULL,
        comment_id        VARCHAR(64)  NOT NULL,
        note_id           VARCHAR(64)  NOT NULL,
        content           TEXT         NOT NULL DEFAULT '',
        like_count        BIGINT       NOT NULL DEFAULT 0,
        is_sub_comment    BOOLEAN      NOT NULL DEFAULT FALSE,
        parent_comment_id VARCHAR(64),
        sub_comment_count INTEGER      NOT NULL DEFAULT 0,
        author            TEXT,
        user_id           VARCHAR(255),
        ip_location       TEXT,
        create_time       BIGINT,
        note_url          TEXT,
        note_title        TEXT,
        crawled_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        UNIQUE (cache_key, comment_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_xcd_cache_note ON xhs_comment_data (cache_key, note_id)",
    "CREATE INDEX IF NOT EXISTS idx_xcd_note_id    ON xhs_comment_data (note_id)",
]

_initialized = False


# ── Cache Key ────────────────────────────────────────────────────────────────

def _make_cache_key(keywords: List[str], time_range: int, min_interaction: int) -> str:
    """用关键词（排序后）+ 时间范围 + 互动量下限 + 分析版本 生成 SHA256 缓存键。

    版本号 "v": 2 确保 v2 全量评论与 v1 首屏评论缓存完全隔离。
    """
    payload = json.dumps(
        {
            "kw": sorted(k.strip().lower() for k in keywords),
            "tr": time_range,
            "mi": min_interaction,
            "v": 2,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ── Table Init ───────────────────────────────────────────────────────────────

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
        logger.info("[comment_cache] xhs_comment_note / xhs_comment_data 表已就绪")
    except Exception as exc:
        logger.warning(f"[comment_cache] 建表失败（将降级跳过缓存）: {exc}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _note_to_row(note: Dict[str, Any], cache_key: str, expires_at: datetime, keywords: List[str], time_range: int) -> Dict[str, Any]:
    """将 pipeline note dict 转换为 xhs_comment_note 的插入行。"""
    return {
        "cache_key":         cache_key,
        "note_id":           str(note.get("note_id") or ""),
        "note_type":         str(note.get("note_type") or ""),
        "title":             str(note.get("title") or ""),
        "description":       str(note.get("desc") or note.get("description") or ""),
        "note_url":          str(note.get("url") or note.get("note_url") or ""),
        "cover_url":         str(note.get("cover_url") or ""),
        "video_url":         str(note.get("video_url") or ""),
        "likes":             int(note.get("likes") or 0),
        "comments":          int(note.get("comments") or 0),
        "collects":          int(note.get("collects") or 0),
        "share_count":       int(note.get("share_count") or 0),
        "interaction_score": int(note.get("interaction_score") or 0),
        "publish_time":      str(note.get("publish_time") or ""),
        "image_urls":        json.dumps(note.get("image_urls") or [], ensure_ascii=False),
        "tag_list":          json.dumps(note.get("tags") or note.get("tag_list") or [], ensure_ascii=False),
        "source_keyword":    str(note.get("source_keyword") or note.get("keyword") or ""),
        "xsec_token":        str(note.get("xsec_token") or ""),
        "user_id":           str(note.get("user_id") or ""),
        "nickname":          str(note.get("nickname") or ""),
        "keywords":          json.dumps(sorted(k.strip() for k in keywords), ensure_ascii=False),
        "time_range":        time_range,
        "expires_at":        expires_at,
    }


def _comment_to_row(comment: Dict[str, Any], cache_key: str) -> Optional[Dict[str, Any]]:
    """将 pipeline comment dict 转换为 xhs_comment_data 的插入行。"""
    cid = str(comment.get("comment_id") or "").strip()
    if not cid:
        return None
    return {
        "cache_key":          cache_key,
        "comment_id":         cid,
        "note_id":            str(comment.get("note_id") or ""),
        "content":            str(comment.get("content") or ""),
        "like_count":         int(comment.get("like_count") or 0),
        "is_sub_comment":     bool(comment.get("is_sub_comment", False)),
        "parent_comment_id":  str(comment.get("parent_comment_id") or "") or None,
        "sub_comment_count":  int(comment.get("sub_comment_count") or 0),
        "author":             str(comment.get("author") or ""),
        "user_id":            str(comment.get("user_id") or ""),
        "ip_location":        str(comment.get("ip_location") or ""),
        "create_time":        comment.get("create_time"),
        "note_url":           str(comment.get("note_url") or ""),
        "note_title":         str(comment.get("note_title") or ""),
    }


def _row_to_note(row: Any) -> Dict[str, Any]:
    """将数据库行还原为 pipeline 所需的 note dict。"""
    return {
        "note_id":           row.note_id,
        "title":             row.title or "",
        "desc":              row.description or "",
        "description":       row.description or "",
        "url":               row.note_url or "",
        "note_url":          row.note_url or "",
        "cover_url":         row.cover_url or "",
        "video_url":         row.video_url or "",
        "likes":             row.likes,
        "comments":          row.comments,
        "collects":          row.collects,
        "share_count":       row.share_count,
        "interaction_score": row.interaction_score,
        "publish_time":      row.publish_time or "",
        "note_type":         row.note_type or "",
        "source_keyword":    row.source_keyword or "",
        "xsec_token":        row.xsec_token or "",
        "nickname":          row.nickname or "",
        "user_id":           row.user_id or "",
        "image_urls":        _safe_json(row.image_urls, []),
        "tags":              _safe_json(row.tag_list, []),
    }


def _row_to_comment(row: Any) -> Dict[str, Any]:
    """将数据库行还原为 pipeline 所需的 comment dict。"""
    return {
        "comment_id":         row.comment_id,
        "content":            row.content or "",
        "like_count":         row.like_count,
        "is_sub_comment":     row.is_sub_comment,
        "parent_comment_id":  row.parent_comment_id or "",
        "sub_comment_count":  row.sub_comment_count,
        "author":             row.author or "",
        "user_id":            row.user_id or "",
        "ip_location":        row.ip_location or "",
        "create_time":        row.create_time,
        "note_id":            row.note_id,
        "note_url":           row.note_url or "",
        "note_title":         row.note_title or "",
    }


def _safe_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# ── Store Class ───────────────────────────────────────────────────────────────

class CommentCrawlCacheStore:
    """评论爬取缓存 CRUD（两表版）。"""

    async def setup(self) -> None:
        """在应用启动时调用（幂等建表）。"""
        await _ensure_table()

    async def get(
        self,
        keywords: List[str],
        time_range: int = 0,
        min_interaction: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """查询缓存。

        命中返回 {"notes": [...{...note_fields, "_cached_comments": [...]}],
                   "note_count": N, "comment_count": M, "from_cache": True, ...}
        未命中返回 None。
        """
        if not _ENABLED:
            return None
        if not _SA_AVAILABLE or not is_postgres_available():
            return None

        await _ensure_table()
        cache_key = _make_cache_key(keywords, time_range, min_interaction)
        now = datetime.now(timezone.utc)

        try:
            async with get_db_session() as session:
                # 1. 查笔记
                note_rows = (await session.execute(
                    sql_text(
                        "SELECT * FROM xhs_comment_note "
                        "WHERE cache_key = :key AND expires_at > :now "
                        "ORDER BY interaction_score DESC"
                    ),
                    {"key": cache_key, "now": now},
                )).fetchall()

                if not note_rows:
                    return None

                oldest_crawled = min(
                    r.crawled_at.replace(tzinfo=timezone.utc) for r in note_rows
                )
                age_h = int((now - oldest_crawled).total_seconds() / 3600)

                # 2. 查评论
                comment_rows = (await session.execute(
                    sql_text(
                        "SELECT * FROM xhs_comment_data "
                        "WHERE cache_key = :key "
                        "ORDER BY note_id, is_sub_comment, like_count DESC"
                    ),
                    {"key": cache_key},
                )).fetchall()

            # 3. 按 note_id 分组评论
            comments_by_note: Dict[str, List[Dict]] = defaultdict(list)
            for row in comment_rows:
                comments_by_note[row.note_id].append(_row_to_comment(row))

            # 4. 还原为 pipeline 所需格式（note dict + _cached_comments）
            notes = []
            for row in note_rows:
                note = _row_to_note(row)
                note["_cached_comments"] = comments_by_note.get(row.note_id, [])
                notes.append(note)

            total_comments = sum(len(v) for v in comments_by_note.values())
            logger.info(
                f"[comment_cache] 命中 key={cache_key[:8]}... "
                f"notes={len(notes)} comments={total_comments} age={age_h}h"
            )
            return {
                "notes":            notes,
                "note_count":       len(notes),
                "comment_count":    total_comments,
                "from_cache":       True,
                "cache_age_hours":  age_h,
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
        """写入/覆盖缓存。失败时静默降级。

        notes 格式：[{...note_fields, "_cached_comments": [...comment_dicts]}, ...]
        """
        if not _ENABLED:
            return
        if not _SA_AVAILABLE or not is_postgres_available():
            return
        if not notes:
            return

        await _ensure_table()
        cache_key = _make_cache_key(keywords, time_range, min_interaction)
        expires_at = datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS)

        try:
            async with get_db_session() as session:
                # 先删除旧数据（同 cache_key），保持数据新鲜
                await session.execute(
                    sql_text("DELETE FROM xhs_comment_data WHERE cache_key = :key"),
                    {"key": cache_key},
                )
                await session.execute(
                    sql_text("DELETE FROM xhs_comment_note WHERE cache_key = :key"),
                    {"key": cache_key},
                )

                total_inserted_notes = 0
                total_inserted_comments = 0

                for note in notes:
                    cached_comments: List[Dict] = note.pop("_cached_comments", [])

                    # 写笔记行
                    note_row = _note_to_row(note, cache_key, expires_at, keywords, time_range)
                    await session.execute(
                        sql_text("""
                            INSERT INTO xhs_comment_note
                                (cache_key, note_id, note_type, title, description,
                                 note_url, cover_url, video_url, likes, comments,
                                 collects, share_count, interaction_score, publish_time,
                                 image_urls, tag_list, source_keyword, xsec_token,
                                 user_id, nickname, keywords, time_range, expires_at)
                            VALUES
                                (:cache_key, :note_id, :note_type, :title, :description,
                                 :note_url, :cover_url, :video_url, :likes, :comments,
                                 :collects, :share_count, :interaction_score, :publish_time,
                                 :image_urls, :tag_list, :source_keyword, :xsec_token,
                                 :user_id, :nickname, CAST(:keywords AS JSONB), :time_range, :expires_at)
                            ON CONFLICT (cache_key, note_id) DO UPDATE SET
                                title             = EXCLUDED.title,
                                description       = EXCLUDED.description,
                                note_url          = EXCLUDED.note_url,
                                likes             = EXCLUDED.likes,
                                comments          = EXCLUDED.comments,
                                collects          = EXCLUDED.collects,
                                share_count       = EXCLUDED.share_count,
                                interaction_score = EXCLUDED.interaction_score,
                                publish_time      = EXCLUDED.publish_time,
                                xsec_token        = EXCLUDED.xsec_token,
                                crawled_at        = NOW(),
                                expires_at        = EXCLUDED.expires_at
                        """),
                        note_row,
                    )
                    total_inserted_notes += 1

                    # 写评论行（批量）
                    note_id = str(note.get("note_id") or "")
                    for comment in cached_comments:
                        comment_row = _comment_to_row(comment, cache_key)
                        if not comment_row:
                            continue
                        if not comment_row.get("note_id"):
                            comment_row["note_id"] = note_id
                        await session.execute(
                            sql_text("""
                                INSERT INTO xhs_comment_data
                                    (cache_key, comment_id, note_id, content, like_count,
                                     is_sub_comment, parent_comment_id, sub_comment_count,
                                     author, user_id, ip_location, create_time,
                                     note_url, note_title)
                                VALUES
                                    (:cache_key, :comment_id, :note_id, :content, :like_count,
                                     :is_sub_comment, :parent_comment_id, :sub_comment_count,
                                     :author, :user_id, :ip_location, :create_time,
                                     :note_url, :note_title)
                                ON CONFLICT (cache_key, comment_id) DO UPDATE SET
                                    content           = EXCLUDED.content,
                                    like_count        = EXCLUDED.like_count,
                                    crawled_at        = NOW()
                            """),
                            comment_row,
                        )
                        total_inserted_comments += 1

                    # 将 _cached_comments 放回（调用方可能还需要用）
                    note["_cached_comments"] = cached_comments

            logger.info(
                f"[comment_cache] 写入 key={cache_key[:8]}... "
                f"notes={total_inserted_notes} comments={total_inserted_comments} ttl={_TTL_DAYS}d"
            )

        except (PostgresUnavailable, Exception) as exc:
            logger.warning(f"[comment_cache] 写入失败（忽略）: {exc}")

    async def delete_expired(self) -> int:
        """清理过期缓存（按笔记表的 expires_at 判断），返回删除的笔记行数。"""
        if not _SA_AVAILABLE or not is_postgres_available():
            return 0
        try:
            async with get_db_session() as session:
                # 找出过期 cache_key
                expired_keys = (await session.execute(
                    sql_text("""
                        SELECT DISTINCT cache_key FROM xhs_comment_note
                        WHERE expires_at <= NOW()
                    """)
                )).fetchall()

                if not expired_keys:
                    return 0

                keys = [r.cache_key for r in expired_keys]
                # 先删评论，再删笔记（避免外键约束，虽然我们没用 FK）
                await session.execute(
                    sql_text("DELETE FROM xhs_comment_data WHERE cache_key = ANY(:keys)"),
                    {"keys": keys},
                )
                result = await session.execute(
                    sql_text("DELETE FROM xhs_comment_note WHERE cache_key = ANY(:keys)"),
                    {"keys": keys},
                )
                deleted = result.rowcount or 0

            if deleted:
                logger.info(f"[comment_cache] 清理过期缓存 {deleted} 条笔记（{len(keys)} 个 key）")
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
                note_row = (await session.execute(sql_text("""
                    SELECT
                        COUNT(*)                                    AS total,
                        COUNT(*) FILTER (WHERE expires_at > NOW()) AS valid,
                        COUNT(DISTINCT cache_key)                   AS cache_keys
                    FROM xhs_comment_note
                """))).fetchone()

                comment_row = (await session.execute(sql_text("""
                    SELECT COUNT(*) AS total FROM xhs_comment_data
                """))).fetchone()

            return {
                "enabled":        _ENABLED,
                "total_notes":    note_row.total,
                "valid_notes":    note_row.valid,
                "cache_keys":     note_row.cache_keys,
                "total_comments": comment_row.total,
                "ttl_days":       _TTL_DAYS,
            }
        except Exception as exc:
            return {"enabled": _ENABLED, "error": str(exc)}


comment_cache_store = CommentCrawlCacheStore()
