"""
笔记向量存储 L2（阶段 4.α,pgvector 后端）。

职责：
- `add_notes(notes)`：批量写入爬到的笔记 + 可选 embedding
- `search_notes(query_text, keywords_filter, ...)`：严格同关键词命中 + 向量排序
- 启动时自动建表/建索引（幂等）
- embedding 可选：若 ModelGateway 的 embed 不可用,allow 笔记入库但不带向量,
  后续检索走纯关键词过滤(按 crawled_at 降序)

命中判定（L2）：
- crawled_at > NOW() - 3 days
- source_keywords && query_keywords (任一关键词重叠)
- 返回 top_k 条,按向量相似度排序(若有 embedding),否则按 crawled_at DESC
- 调用方再判 "命中条数 ≥ 10" 才算 L2 命中

env：
- NOTES_VECTOR_DIM          嵌入维度,默认 1024(text-embedding-v4)
- NOTES_VECTOR_RECENT_DAYS  L2 有效时间窗,默认 3 天
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


try:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    sql_text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False

from .db_engine import (
    PostgresUnavailable,
    get_db_engine,
    get_db_session,
    is_postgres_available,
)


_VECTOR_DIM = int(os.getenv("NOTES_VECTOR_DIM", "1024"))
_RECENT_DAYS = int(os.getenv("NOTES_VECTOR_RECENT_DAYS", "3"))


# 启动幂等建表（docker-compose 的 init SQL 是兜底,纯本地 Postgres 也能用）
_INIT_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS xhs_notes (
    note_id           VARCHAR(64)    PRIMARY KEY,
    title             TEXT           NOT NULL DEFAULT '',
    "desc"            TEXT           NOT NULL DEFAULT '',
    url               TEXT           NOT NULL DEFAULT '',
    cover_url         TEXT,
    image_urls        TEXT[]         NOT NULL DEFAULT '{{}}',
    video_url         TEXT,
    likes             INTEGER        NOT NULL DEFAULT 0,
    comments          INTEGER        NOT NULL DEFAULT 0,
    collects          INTEGER        NOT NULL DEFAULT 0,
    share_count       INTEGER        NOT NULL DEFAULT 0,
    interaction_score INTEGER        NOT NULL DEFAULT 0,
    metrics_precise   BOOLEAN        NOT NULL DEFAULT FALSE,
    media_type        VARCHAR(16)    NOT NULL DEFAULT 'image',
    note_type         VARCHAR(16)    NOT NULL DEFAULT '',
    nickname          VARCHAR(128)   NOT NULL DEFAULT '',
    source_keywords   TEXT[]         NOT NULL DEFAULT '{{}}',
    crawled_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    embedding         vector({_VECTOR_DIM})
);

CREATE INDEX IF NOT EXISTS xhs_notes_embedding_hnsw
    ON xhs_notes USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS xhs_notes_source_keywords_gin
    ON xhs_notes USING gin (source_keywords);

CREATE INDEX IF NOT EXISTS xhs_notes_crawled_at_idx
    ON xhs_notes (crawled_at DESC);

ALTER TABLE xhs_notes ADD COLUMN IF NOT EXISTS published_at TEXT DEFAULT '';
"""


class NotesVectorStore:
    """笔记向量存储 L2（pgvector）。"""

    def __init__(self) -> None:
        self._initialized = False

    async def ensure_schema(self) -> None:
        """幂等建表（首次调用执行）。失败时抛 PostgresUnavailable。"""
        if self._initialized:
            return
        if not _SA_AVAILABLE:
            raise PostgresUnavailable("sqlalchemy[asyncio] 未安装")

        engine = await get_db_engine()
        async with engine.begin() as conn:
            # 拆分逐条执行（避免某些驱动不支持多语句）
            for stmt in filter(None, (s.strip() for s in _INIT_SQL.split(";"))):
                if not stmt:
                    continue
                await conn.execute(sql_text(stmt))
        self._initialized = True
        logger.info("[NotesVectorStore] pgvector 表和索引就绪")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add_notes(
        self,
        notes: List[Dict[str, Any]],
        embeddings: Optional[List[List[float]]] = None,
    ) -> int:
        """批量 UPSERT 笔记到 L2。

        Args:
            notes:       CrawlerAgent normalize 出来的 dict 列表
            embeddings:  可选,和 notes 同长度;None 表示不带向量入库

        Returns:
            成功写入的条数（已存在的走 ON CONFLICT 更新）
        """
        if not notes:
            return 0

        try:
            await self.ensure_schema()
        except PostgresUnavailable as exc:
            logger.warning(f"[NotesVectorStore.add_notes] pgvector 不可用,跳过写入: {exc}")
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[NotesVectorStore.add_notes] schema 初始化失败: {exc}")
            return 0

        if embeddings is not None and len(embeddings) != len(notes):
            logger.warning(
                f"[NotesVectorStore.add_notes] embeddings 数量 {len(embeddings)} "
                f"与 notes {len(notes)} 不一致,忽略 embeddings"
            )
            embeddings = None

        rows: List[Dict[str, Any]] = []
        for idx, note in enumerate(notes):
            note_id = str(note.get("note_id") or "").strip()
            if not note_id:
                continue  # 没 id 不存

            emb = embeddings[idx] if embeddings else None
            rows.append(
                {
                    "note_id": note_id,
                    "title": _str(note.get("title"), ""),
                    "desc_col": _str(note.get("desc"), ""),
                    "url": _str(note.get("url"), ""),
                    "cover_url": _str(note.get("cover_url"), None),
                    "image_urls": list(note.get("image_urls") or []),
                    "video_url": _str(note.get("video_url"), None),
                    "likes": _int(note.get("likes"), 0),
                    "comments": _int(note.get("comments"), 0),
                    "collects": _int(note.get("collects"), 0),
                    "share_count": _int(note.get("share_count"), 0),
                    "interaction_score": _int(note.get("interaction_score"), 0),
                    "metrics_precise": bool(note.get("metrics_precise", False)),
                    "media_type": _str(note.get("media_type"), "image"),
                    "note_type": _str(note.get("note_type"), ""),
                    "nickname": _str(note.get("nickname"), ""),
                    "source_keywords": list(note.get("source_keywords") or []),
                    "published_at": _str(note.get("published_at"), ""),
                    "embedding": _format_vector(emb) if emb is not None else None,
                }
            )

        if not rows:
            return 0

        # 注意：使用 CAST(:name AS type) 显式转换,避免 SQLAlchemy 在
        # "命名参数 + PG 双冒号类型转换" 混合写法下出现解析歧义,
        # 否则 asyncpg 会拿到未翻译的命名参数并抛 syntax error。
        #
        # 另一道坑：CASE WHEN 的两个分支必须对 :embedding 给出 **同样** 的类型约束,
        # 否则当参数为 NULL 时 PostgreSQL 无法推断 $N 的数据类型,会抛
        # `could not determine data type of parameter`。因此 IS NULL 分支
        # 也 CAST 成 text 再比较,和 ELSE 分支 CAST(... AS vector) 的 input 类型对齐。
        upsert_sql = sql_text(
            """
            INSERT INTO xhs_notes (
                note_id, title, "desc", url, cover_url, image_urls, video_url,
                likes, comments, collects, share_count, interaction_score, metrics_precise,
                media_type, note_type, nickname, source_keywords, published_at, embedding, updated_at
            ) VALUES (
                :note_id, :title, :desc_col, :url, :cover_url, :image_urls, :video_url,
                :likes, :comments, :collects, :share_count, :interaction_score, :metrics_precise,
                :media_type, :note_type, :nickname, :source_keywords, :published_at,
                CASE WHEN CAST(:embedding AS text) IS NULL
                     THEN NULL
                     ELSE CAST(:embedding AS vector)
                END,
                NOW()
            )
            ON CONFLICT (note_id) DO UPDATE SET
                title = EXCLUDED.title,
                "desc" = EXCLUDED."desc",
                url = EXCLUDED.url,
                cover_url = EXCLUDED.cover_url,
                image_urls = EXCLUDED.image_urls,
                video_url = EXCLUDED.video_url,
                likes = EXCLUDED.likes,
                comments = EXCLUDED.comments,
                collects = EXCLUDED.collects,
                share_count = EXCLUDED.share_count,
                interaction_score = EXCLUDED.interaction_score,
                metrics_precise = EXCLUDED.metrics_precise,
                media_type = EXCLUDED.media_type,
                note_type = EXCLUDED.note_type,
                nickname = EXCLUDED.nickname,
                source_keywords = (
                    SELECT ARRAY(SELECT DISTINCT UNNEST(xhs_notes.source_keywords || EXCLUDED.source_keywords))
                ),
                published_at = CASE WHEN EXCLUDED.published_at != '' THEN EXCLUDED.published_at ELSE xhs_notes.published_at END,
                embedding = COALESCE(EXCLUDED.embedding, xhs_notes.embedding),
                updated_at = NOW()
            """
        )

        try:
            async with get_db_session() as session:
                await session.execute(upsert_sql, rows)
            logger.debug(f"[NotesVectorStore.add_notes] upsert {len(rows)} 条")
            return len(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[NotesVectorStore.add_notes] 写入失败: {exc}")
            return 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def search_notes(
        self,
        keywords: List[str],
        top_k: int = 30,
        query_embedding: Optional[List[float]] = None,
        recent_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """L2 命中查询。

        Args:
            keywords:         必传,用于严格同词命中过滤（source_keywords && input）
            top_k:            返回上限
            query_embedding:  可选,有则按向量相似度排序;否则按互动分降序
            recent_days:      覆盖默认 3 天窗口

        Returns:
            命中的笔记 dict 列表,结构与 CrawlerAgent normalize 后的 dict 一致。
            调用方判 len(返回) >= 10 才算命中 L2。
        """
        if not keywords:
            return []

        try:
            await self.ensure_schema()
        except PostgresUnavailable:
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[NotesVectorStore.search_notes] schema 检查失败: {exc}")
            return []

        window_days = recent_days if recent_days is not None else _RECENT_DAYS
        min_crawled_at = datetime.now(timezone.utc) - timedelta(days=window_days)

        cleaned_keywords = [str(k).strip() for k in keywords if str(k).strip()]
        if not cleaned_keywords:
            return []

        params: Dict[str, Any] = {
            "keywords": cleaned_keywords,
            "min_crawled_at": min_crawled_at,
            "limit": top_k,
        }

        if query_embedding is not None:
            params["query_vec"] = _format_vector(query_embedding)
            order_clause = "embedding <-> CAST(:query_vec AS vector) ASC NULLS LAST, interaction_score DESC"
        else:
            order_clause = "interaction_score DESC, crawled_at DESC"

        query_sql = sql_text(
            f"""
            SELECT
                note_id, title, "desc" AS desc_col, url, cover_url, image_urls, video_url,
                likes, comments, collects, share_count, interaction_score, metrics_precise,
                media_type, note_type, nickname, source_keywords,
                published_at, crawled_at
            FROM xhs_notes
            WHERE source_keywords && CAST(:keywords AS text[])
              AND crawled_at > :min_crawled_at
            ORDER BY {order_clause}
            LIMIT :limit
            """
        )

        try:
            async with get_db_session() as session:
                result = await session.execute(query_sql, params)
                rows = result.mappings().all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[NotesVectorStore.search_notes] 查询失败: {exc}")
            return []

        return [_row_to_note_dict(row) for row in rows]

    async def count_recent_for_keywords(
        self,
        keywords: List[str],
        recent_days: Optional[int] = None,
    ) -> int:
        """便捷：只查"过去 N 天内 source_keywords 重合"的笔记数量,供健康诊断用。"""
        if not keywords:
            return 0
        try:
            await self.ensure_schema()
        except Exception:
            return 0

        window_days = recent_days if recent_days is not None else _RECENT_DAYS
        min_crawled_at = datetime.now(timezone.utc) - timedelta(days=window_days)

        count_sql = sql_text(
            """
            SELECT COUNT(*) AS n FROM xhs_notes
            WHERE source_keywords && CAST(:keywords AS text[])
              AND crawled_at > :min_crawled_at
            """
        )
        try:
            async with get_db_session() as session:
                result = await session.execute(
                    count_sql,
                    {"keywords": list(keywords), "min_crawled_at": min_crawled_at},
                )
                return int(result.scalar() or 0)
        except Exception:  # noqa: BLE001
            return 0


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


def _str(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return str(value)


def _int(value: Any, default: int) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _format_vector(vec: List[float]) -> str:
    """pgvector 字面量格式: "[0.1,0.2,0.3]"（用 :: vector 转换）。"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _row_to_note_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """SQL 行 → CrawlerAgent 下游 Agent 能直接用的 dict。"""
    return {
        "note_id": row.get("note_id", ""),
        "title": row.get("title", ""),
        "desc": row.get("desc_col", ""),
        "url": row.get("url", ""),
        "cover_url": row.get("cover_url") or "",
        "image_urls": list(row.get("image_urls") or []),
        "video_url": row.get("video_url") or "",
        "likes": int(row.get("likes") or 0),
        "comments": int(row.get("comments") or 0),
        "collects": int(row.get("collects") or 0),
        "share_count": int(row.get("share_count") or 0),
        "interaction_score": int(row.get("interaction_score") or 0),
        "metrics_precise": bool(row.get("metrics_precise") or False),
        "media_type": row.get("media_type") or "image",
        "note_type": row.get("note_type") or "",
        "nickname": row.get("nickname") or "",
        "source_keywords": list(row.get("source_keywords") or []),
        "published_at": row.get("published_at") or "",
        "dimensions_hit": [],  # L2 路径不携带维度信息,由调用方按需补充
        "keyword": "",
        "dimension": "",
    }


_default_store: Optional[NotesVectorStore] = None


def get_notes_vector_store() -> NotesVectorStore:
    global _default_store
    if _default_store is None:
        _default_store = NotesVectorStore()
    return _default_store


__all__ = [
    "NotesVectorStore",
    "get_notes_vector_store",
]
