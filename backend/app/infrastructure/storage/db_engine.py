"""
全局 SQLAlchemy async engine（阶段 4.α,仅用于 pgvector）。

约定：
- 本 engine **只服务于 pgvector 笔记向量存储**,业务数据仍走 JSON
- 阶段 4.6 业务表迁入后本 engine 会承担全部 DB 访问
- 懒加载单例 + `get_db_session()` 作 async context manager

env：
- POSTGRES_DSN        完整 DSN,如 "postgresql+asyncpg://redmuse:redmuse_dev@localhost:5432/redmuse"
  若未设置,自动从 POSTGRES_HOST/PORT/USER/PASSWORD/DB 拼
- POSTGRES_POOL_SIZE  连接池 base size,默认 5
- POSTGRES_MAX_OVERFLOW 连接池溢出上限,默认 10
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional

from loguru import logger


try:
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    AsyncEngine = None  # type: ignore[misc,assignment]
    AsyncSession = None  # type: ignore[misc,assignment]
    async_sessionmaker = None  # type: ignore[misc,assignment]
    create_async_engine = None  # type: ignore[misc,assignment]
    SQLAlchemyError = Exception  # type: ignore[misc,assignment]
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


class PostgresUnavailable(RuntimeError):
    """PostgreSQL 不可用（未装依赖 / 连不上 / DSN 配错）。"""


@dataclass(frozen=True)
class PostgresSettings:
    dsn: str
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False

    @classmethod
    def from_env(cls) -> "PostgresSettings":
        dsn = (os.getenv("POSTGRES_DSN") or "").strip()
        if not dsn:
            host = os.getenv("POSTGRES_HOST", "localhost").strip() or "localhost"
            port = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"
            user = os.getenv("POSTGRES_USER", "redmuse").strip() or "redmuse"
            pwd = os.getenv("POSTGRES_PASSWORD", "redmuse_dev").strip() or "redmuse_dev"
            db = os.getenv("POSTGRES_DB", "redmuse").strip() or "redmuse"
            dsn = f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"
        return cls(
            dsn=dsn,
            pool_size=int(os.getenv("POSTGRES_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("POSTGRES_MAX_OVERFLOW", "10")),
            echo=os.getenv("POSTGRES_ECHO", "false").lower() in ("1", "true", "yes"),
        )


_engine: Optional["AsyncEngine"] = None
_session_factory: Optional["async_sessionmaker[AsyncSession]"] = None
_settings: Optional[PostgresSettings] = None


def is_postgres_available() -> bool:
    return _SA_AVAILABLE


async def get_db_engine(settings: Optional[PostgresSettings] = None) -> "AsyncEngine":
    """获取全局 async engine（懒加载）。"""
    global _engine, _session_factory, _settings

    if not _SA_AVAILABLE:
        raise PostgresUnavailable("sqlalchemy[asyncio] 未安装")

    if _engine is not None:
        return _engine

    cfg = settings or PostgresSettings.from_env()
    _settings = cfg

    try:
        _engine = create_async_engine(
            cfg.dsn,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            echo=cfg.echo,
            pool_pre_ping=True,  # 连接存活预检,防止 Postgres 重启后拿到死连接
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

        # 启动连通性检查
        async with _engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info(f"[DBEngine] pgvector 已连通: {_mask_dsn(cfg.dsn)}")
        return _engine
    except SQLAlchemyError as exc:  # pragma: no cover
        _engine = None
        _session_factory = None
        raise PostgresUnavailable(f"PostgreSQL 连接失败: {exc}") from exc


@asynccontextmanager
async def get_db_session() -> AsyncIterator["AsyncSession"]:
    """Async context manager 拿一个 session。

    Example:
        async with get_db_session() as session:
            result = await session.execute(text("SELECT 1"))
    """
    if _session_factory is None:
        await get_db_engine()
    assert _session_factory is not None

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def close_db_engine() -> None:
    """FastAPI shutdown 释放连接池。"""
    global _engine, _session_factory, _settings
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[DBEngine] dispose 失败: {exc}")
    _engine = None
    _session_factory = None
    _settings = None


async def check_postgres_health() -> Dict[str, object]:
    """健康检查：{status, latency_ms, dsn_masked}。"""
    if not _SA_AVAILABLE:
        return {"status": "unavailable", "reason": "sqlalchemy_dep_missing"}

    import time

    start = time.monotonic()
    try:
        engine = await get_db_engine()
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "healthy",
            "latency_ms": latency_ms,
            "dsn": _mask_dsn(_settings.dsn) if _settings else "unknown",
        }
    except PostgresUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except SQLAlchemyError as exc:  # pragma: no cover
        return {"status": "error", "reason": str(exc)}


def _mask_dsn(dsn: str) -> str:
    """遮蔽密码,方便日志输出。"""
    try:
        if "://" not in dsn:
            return dsn
        scheme, rest = dsn.split("://", 1)
        if "@" not in rest:
            return dsn
        cred, host_part = rest.split("@", 1)
        if ":" in cred:
            user, _pwd = cred.split(":", 1)
            return f"{scheme}://{user}:***@{host_part}"
        return dsn
    except Exception:
        return "<dsn-masked>"


__all__ = [
    "PostgresSettings",
    "PostgresUnavailable",
    "get_db_engine",
    "get_db_session",
    "close_db_engine",
    "check_postgres_health",
    "is_postgres_available",
]
