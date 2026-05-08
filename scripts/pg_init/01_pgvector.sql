-- RedMuse PostgreSQL 初始化脚本（阶段 4.α）
-- Postgres 镜像 pgvector/pgvector:pg16 启动时会自动执行 /docker-entrypoint-initdb.d/*.sql
--
-- 职责：
--   1. 启用 pgvector 扩展
--   2. 创建 xhs_notes 表（存爬到的笔记向量 + 元数据,用于 L2 缓存检索）
--   3. HNSW 向量索引 + GIN 关键词数组索引
--
-- 注：业务表（tasks / identities / knowledge_documents / ...）等阶段 4.6 再统一迁入。

CREATE EXTENSION IF NOT EXISTS vector;

-- ======================================================================
-- 小红书笔记向量存储（L2 缓存层）
-- ======================================================================
--
-- 约定：
--   - embedding 维度 1024：对齐阿里云 text-embedding-v4
--     （如升级模型,请修改这里的维度 + 重建索引 + 迁移旧数据）
--   - source_keywords：严格同关键词命中必备字段,GIN 索引加速 && 查询
--   - crawled_at：3 天内爬到的才算 L2 有效缓存(CrawlerAgent 层控制)
CREATE TABLE IF NOT EXISTS xhs_notes (
    note_id           VARCHAR(64)    PRIMARY KEY,
    title             TEXT           NOT NULL DEFAULT '',
    "desc"            TEXT           NOT NULL DEFAULT '',
    url               TEXT           NOT NULL DEFAULT '',
    cover_url         TEXT,
    image_urls        TEXT[]         NOT NULL DEFAULT '{}',
    video_url         TEXT,

    -- 互动数据（来自详情接口的精确值）
    likes             INTEGER        NOT NULL DEFAULT 0,
    comments          INTEGER        NOT NULL DEFAULT 0,
    collects          INTEGER        NOT NULL DEFAULT 0,
    share_count       INTEGER        NOT NULL DEFAULT 0,
    interaction_score INTEGER        NOT NULL DEFAULT 0,
    metrics_precise   BOOLEAN        NOT NULL DEFAULT FALSE,

    -- 分类字段
    media_type        VARCHAR(16)    NOT NULL DEFAULT 'image',   -- image | video
    note_type         VARCHAR(16)    NOT NULL DEFAULT '',        -- 图集 | 视频
    nickname          VARCHAR(128)   NOT NULL DEFAULT '',

    -- 关键：用于严格同关键词命中判定（GIN 索引)
    source_keywords   TEXT[]         NOT NULL DEFAULT '{}',

    -- 元数据
    crawled_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- 向量（对齐 text-embedding-v4 的 1024 维）
    embedding         vector(1024)
);

-- HNSW 向量索引（cosine 距离）
-- 注：初次写入量少时 HNSW 性能优势不明显,量起来后自动发挥
CREATE INDEX IF NOT EXISTS xhs_notes_embedding_hnsw
    ON xhs_notes
    USING hnsw (embedding vector_cosine_ops);

-- GIN 关键词数组索引（source_keywords 严格同词命中）
CREATE INDEX IF NOT EXISTS xhs_notes_source_keywords_gin
    ON xhs_notes
    USING gin (source_keywords);

-- 时间过滤索引（3 天内爬到的笔记才算 L2 有效）
CREATE INDEX IF NOT EXISTS xhs_notes_crawled_at_idx
    ON xhs_notes (crawled_at DESC);

-- 标题全文模糊搜索索引（辅助查询,非必需）
CREATE INDEX IF NOT EXISTS xhs_notes_title_trgm_idx
    ON xhs_notes (title);

COMMENT ON TABLE xhs_notes IS 'L2 缓存：历史爬到的小红书笔记,3 天内 + 同关键词命中 ≥10 条时可避免 L3 实时爬';
COMMENT ON COLUMN xhs_notes.source_keywords IS '采集时对应的关键词列表,用于严格同词命中判定';
COMMENT ON COLUMN xhs_notes.embedding IS 'text-embedding-v4 1024 维向量';
COMMENT ON COLUMN xhs_notes.metrics_precise IS 'TRUE=来自详情接口精确值 / FALSE=搜索列表下界值(如 "100+")';
