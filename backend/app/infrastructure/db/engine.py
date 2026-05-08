"""Synchronous SQLAlchemy engine for business tables.

The existing async engine in ``infrastructure.storage.db_engine`` continues to
serve pgvector note-cache code. Phase 4.6 keeps current store method signatures
synchronous, so business repositories use a small sync engine/session layer.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

from loguru import logger

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session, sessionmaker

    _SA_SYNC_AVAILABLE = True
except ImportError:  # pragma: no cover
    Engine = None  # type: ignore[assignment,misc]
    Session = None  # type: ignore[assignment,misc]
    sessionmaker = None  # type: ignore[assignment,misc]
    create_engine = None  # type: ignore[assignment]
    SQLAlchemyError = Exception  # type: ignore[assignment,misc]
    text = None  # type: ignore[assignment]
    _SA_SYNC_AVAILABLE = False


class BusinessDbUnavailable(RuntimeError):
    """Business PostgreSQL is unavailable or misconfigured."""


@dataclass(frozen=True)
class BusinessPostgresSettings:
    dsn: str
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False

    @classmethod
    def from_env(cls) -> "BusinessPostgresSettings":
        raw = (os.getenv("BUSINESS_POSTGRES_DSN") or os.getenv("POSTGRES_DSN") or "").strip()
        if not raw:
            host = os.getenv("POSTGRES_HOST", "localhost").strip() or "localhost"
            port = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"
            user = os.getenv("POSTGRES_USER", "redmuse").strip() or "redmuse"
            pwd = os.getenv("POSTGRES_PASSWORD", "redmuse_dev").strip() or "redmuse_dev"
            db = os.getenv("POSTGRES_DB", "redmuse").strip() or "redmuse"
            raw = f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"
        dsn = _to_sync_dsn(raw)
        return cls(
            dsn=dsn,
            pool_size=int(os.getenv("BUSINESS_POSTGRES_POOL_SIZE", os.getenv("POSTGRES_POOL_SIZE", "5"))),
            max_overflow=int(
                os.getenv("BUSINESS_POSTGRES_MAX_OVERFLOW", os.getenv("POSTGRES_MAX_OVERFLOW", "10"))
            ),
            echo=os.getenv("POSTGRES_ECHO", "false").lower() in ("1", "true", "yes"),
        )


_engine: Optional["Engine"] = None
_session_factory: Optional["sessionmaker[Session]"] = None
_settings: Optional[BusinessPostgresSettings] = None


def get_business_db_engine(settings: Optional[BusinessPostgresSettings] = None) -> "Engine":
    global _engine, _session_factory, _settings
    if not _SA_SYNC_AVAILABLE:
        raise BusinessDbUnavailable("sqlalchemy/psycopg 依赖未安装")
    if _engine is not None:
        return _engine

    cfg = settings or BusinessPostgresSettings.from_env()
    try:
        _engine = create_engine(
            cfg.dsn,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            echo=cfg.echo,
            pool_pre_ping=True,
            future=True,
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _settings = cfg
        logger.info(f"[BusinessDB] 已连接: {_mask_dsn(cfg.dsn)}")
        return _engine
    except SQLAlchemyError as exc:  # pragma: no cover - depends on external DB
        _engine = None
        _session_factory = None
        raise BusinessDbUnavailable(f"Business PostgreSQL 连接失败: {exc}") from exc


@contextmanager
def get_business_db_session() -> Iterator["Session"]:
    if _session_factory is None:
        get_business_db_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def close_business_db_engine() -> None:
    global _engine, _session_factory, _settings
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[BusinessDB] dispose 失败: {exc}")
    _engine = None
    _session_factory = None
    _settings = None


def check_business_db_health() -> Dict[str, object]:
    if not _SA_SYNC_AVAILABLE:
        return {"status": "unavailable", "reason": "sync_sqlalchemy_dep_missing"}
    import time

    start = time.monotonic()
    try:
        engine = get_business_db_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "latency_ms": int((time.monotonic() - start) * 1000),
            "dsn": _mask_dsn(_settings.dsn) if _settings else "unknown",
        }
    except BusinessDbUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except SQLAlchemyError as exc:  # pragma: no cover
        return {"status": "error", "reason": str(exc)}


def _to_sync_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + dsn.split("://", 1)[1]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn.split("://", 1)[1]
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn.split("://", 1)[1]
    return dsn


def _mask_dsn(dsn: str) -> str:
    try:
        if "://" not in dsn or "@" not in dsn:
            return dsn
        scheme, rest = dsn.split("://", 1)
        cred, host_part = rest.split("@", 1)
        if ":" in cred:
            user, _pwd = cred.split(":", 1)
            return f"{scheme}://{user}:***@{host_part}"
        return dsn
    except Exception:
        return "<dsn-masked>"
