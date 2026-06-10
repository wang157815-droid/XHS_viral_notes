"""
MySQL xhs_note / xhs_note_comment → PostgreSQL xhs_comment_note / xhs_comment_data
迁移脚本（一次性运行）。

用法：
    # 确保 .env 已配置 PostgreSQL 连接，同时配置 MySQL 连接参数
    python scripts/migrate_mysql_to_pg.py

环境变量（MySQL 源）：
    MYSQL_HOST      默认 localhost
    MYSQL_PORT      默认 3306
    MYSQL_USER      默认 root
    MYSQL_PASSWORD  必填
    MYSQL_DATABASE  默认 media_crawler

环境变量（PostgreSQL 目标，复用项目 .env）：
    DATABASE_URL 或 POSTGRES_* 变量（与后端配置一致）
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 确保能 import 项目模块
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from xhs_utils.common_util import load_env  # noqa: E402

load_env()


# ── MySQL 连接 ────────────────────────────────────────────────────────────────

MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER     = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "media_crawler")

# 导入后的缓存有效期（天）
IMPORT_TTL_DAYS = int(os.getenv("IMPORT_TTL_DAYS", "365"))


def _make_cache_key_from_keyword(keyword: str) -> str:
    """用单关键词生成 cache_key（与 comment_cache_store 算法一致）。"""
    payload = json.dumps(
        {"kw": [keyword.strip().lower()], "tr": 0, "mi": 0, "v": 2},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _calc_interaction_score(liked: int, comments: int, collected: int) -> int:
    return liked + comments + collected


def _safe_int(v) -> int:
    try:
        return int(str(v).replace("+", "").strip()) if v else 0
    except (ValueError, TypeError):
        return 0


async def migrate():
    try:
        import aiomysql
    except ImportError:
        logger.error("请先安装 aiomysql：pip install aiomysql")
        return

    from backend.app.infrastructure.storage.db_engine import get_db_session, is_postgres_available
    from sqlalchemy import text as sql_text

    if not is_postgres_available():
        logger.error("PostgreSQL 不可用，请检查 DATABASE_URL 配置")
        return

    logger.info(f"连接 MySQL: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}")
    mysql_conn = await aiomysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        db=MYSQL_DATABASE,
        charset="utf8mb4",
    )

    expires_at = datetime.now(timezone.utc) + timedelta(days=IMPORT_TTL_DAYS)

    try:
        async with mysql_conn.cursor(aiomysql.DictCursor) as cur:
            # ── 读取所有笔记 ──────────────────────────────────────────────────
            await cur.execute("SELECT * FROM xhs_note")
            notes = await cur.fetchall()
            logger.info(f"从 MySQL 读取 {len(notes)} 条笔记")

            # ── 读取所有评论 ──────────────────────────────────────────────────
            await cur.execute("SELECT * FROM xhs_note_comment")
            comments = await cur.fetchall()
            logger.info(f"从 MySQL 读取 {len(comments)} 条评论")

    finally:
        mysql_conn.close()

    # ── 按 source_keyword 分组笔记，生成 cache_key ────────────────────────────
    keyword_map: dict[str, str] = {}   # source_keyword → cache_key
    note_cache_key_map: dict[str, str] = {}  # note_id → cache_key

    for note in notes:
        kw = str(note.get("source_keyword") or "imported").strip() or "imported"
        if kw not in keyword_map:
            keyword_map[kw] = _make_cache_key_from_keyword(kw)
        note_cache_key_map[str(note["note_id"])] = keyword_map[kw]

    logger.info(f"识别到 {len(keyword_map)} 个关键词分组: {list(keyword_map.keys())}")

    # ── 写入 PostgreSQL ───────────────────────────────────────────────────────
    note_ok = note_skip = comment_ok = comment_skip = 0

    async with get_db_session() as session:
        # 确保目标表存在
        from backend.app.infrastructure.storage.comment_cache_store import _INIT_STATEMENTS, _initialized
        for stmt in _INIT_STATEMENTS:
            await session.execute(sql_text(stmt))

        # 写笔记
        for note in notes:
            note_id = str(note.get("note_id") or "").strip()
            if not note_id:
                note_skip += 1
                continue

            cache_key = note_cache_key_map.get(note_id, "imported_default")
            source_kw = str(note.get("source_keyword") or "")

            liked     = _safe_int(note.get("liked_count"))
            collected = _safe_int(note.get("collected_count"))
            cmt       = _safe_int(note.get("comment_count"))
            share     = _safe_int(note.get("share_count"))
            score     = _calc_interaction_score(liked, cmt, collected)

            # 发布时间：MySQL time 字段是毫秒时间戳
            publish_time = ""
            ts = note.get("time")
            if ts:
                try:
                    t = int(ts)
                    t_sec = t // 1000 if t > 1e10 else t
                    publish_time = datetime.fromtimestamp(t_sec).strftime("%Y-%m-%d")
                except Exception:
                    pass

            try:
                await session.execute(sql_text("""
                    INSERT INTO xhs_comment_note
                        (cache_key, note_id, note_type, title, description,
                         note_url, cover_url, video_url, likes, comments,
                         collects, share_count, interaction_score, publish_time,
                         image_urls, tag_list, source_keyword, xsec_token,
                         user_id, nickname,
                         keywords, time_range, expires_at)
                    VALUES
                        (:cache_key, :note_id, :note_type, :title, :description,
                         :note_url, '', :video_url, :likes, :comments,
                         :collects, :share_count, :interaction_score, :publish_time,
                         :image_urls, :tag_list, :source_keyword, :xsec_token,
                         :user_id, :nickname,
                         CAST(:keywords AS JSONB), 0, :expires_at)
                    ON CONFLICT (cache_key, note_id) DO UPDATE SET
                        title             = EXCLUDED.title,
                        description       = EXCLUDED.description,
                        likes             = EXCLUDED.likes,
                        comments          = EXCLUDED.comments,
                        collects          = EXCLUDED.collects,
                        share_count       = EXCLUDED.share_count,
                        interaction_score = EXCLUDED.interaction_score,
                        publish_time      = EXCLUDED.publish_time,
                        expires_at        = EXCLUDED.expires_at
                """), {
                    "cache_key":         cache_key,
                    "note_id":           note_id,
                    "note_type":         str(note.get("type") or ""),
                    "title":             str(note.get("title") or ""),
                    "description":       str(note.get("desc") or ""),
                    "note_url":          str(note.get("note_url") or ""),
                    "video_url":         str(note.get("video_url") or ""),
                    "likes":             liked,
                    "comments":          cmt,
                    "collects":          collected,
                    "share_count":       share,
                    "interaction_score": score,
                    "publish_time":      publish_time,
                    "image_urls":        str(note.get("image_list") or "[]"),
                    "tag_list":          str(note.get("tag_list") or "[]"),
                    "source_keyword":    source_kw,
                    "xsec_token":        str(note.get("xsec_token") or ""),
                    "user_id":           str(note.get("user_id") or ""),
                    "nickname":          str(note.get("nickname") or ""),
                    "keywords":          json.dumps([source_kw], ensure_ascii=False),
                    "expires_at":        expires_at,
                })
                note_ok += 1
            except Exception as exc:
                logger.warning(f"笔记 {note_id} 写入失败: {exc}")
                note_skip += 1

        logger.info(f"笔记写入完成：成功 {note_ok} / 跳过 {note_skip}")

        # 写评论
        for comment in comments:
            comment_id = str(comment.get("comment_id") or "").strip()
            note_id    = str(comment.get("note_id") or "").strip()
            if not comment_id or not note_id:
                comment_skip += 1
                continue

            cache_key = note_cache_key_map.get(note_id, "imported_default")
            parent_id = str(comment.get("parent_comment_id") or "").strip()
            is_sub    = parent_id not in ("", "0")

            try:
                await session.execute(sql_text("""
                    INSERT INTO xhs_comment_data
                        (cache_key, comment_id, note_id, content, like_count,
                         is_sub_comment, parent_comment_id, sub_comment_count,
                         author, user_id, ip_location, create_time,
                         note_url, note_title)
                    VALUES
                        (:cache_key, :comment_id, :note_id, :content, :like_count,
                         :is_sub_comment, :parent_comment_id, :sub_comment_count,
                         :author, :user_id, :ip_location, :create_time,
                         '', '')
                    ON CONFLICT (cache_key, comment_id) DO NOTHING
                """), {
                    "cache_key":          cache_key,
                    "comment_id":         comment_id,
                    "note_id":            note_id,
                    "content":            str(comment.get("content") or ""),
                    "like_count":         _safe_int(comment.get("like_count")),
                    "is_sub_comment":     is_sub,
                    "parent_comment_id":  parent_id if is_sub else None,
                    "sub_comment_count":  int(comment.get("sub_comment_count") or 0),
                    "author":             str(comment.get("nickname") or ""),
                    "user_id":            str(comment.get("user_id") or ""),
                    "ip_location":        str(comment.get("ip_location") or ""),
                    "create_time":        comment.get("create_time"),
                })
                comment_ok += 1
            except Exception as exc:
                logger.warning(f"评论 {comment_id} 写入失败: {exc}")
                comment_skip += 1

        logger.info(f"评论写入完成：成功 {comment_ok} / 跳过 {comment_skip}")

    logger.success(
        f"\n迁移完成！\n"
        f"  笔记：{note_ok} 条成功，{note_skip} 条跳过\n"
        f"  评论：{comment_ok} 条成功，{comment_skip} 条跳过\n"
        f"  关键词分组：{list(keyword_map.items())}\n"
        f"  缓存有效期：{IMPORT_TTL_DAYS} 天"
    )


if __name__ == "__main__":
    asyncio.run(migrate())
