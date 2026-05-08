from datetime import datetime, timezone

from fastapi import APIRouter

from ...core.config import settings
from ...core.responses import ok
from ...infrastructure.cache.redis_client import check_redis_health
from ...infrastructure.db import check_business_db_health
from ...infrastructure.storage.db_engine import check_postgres_health

router = APIRouter(tags=["health"])


@router.get("/health")
async def healthcheck():
    business_db = check_business_db_health()
    redis = await check_redis_health()
    pgvector = await check_postgres_health()
    ready = business_db.get("status") == "healthy" and redis.get("status") == "healthy"
    return ok(
        {
            "service": settings.app_name,
            "version": settings.app_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "up" if ready else "degraded",
            "business_db": business_db,
            "redis": redis,
            "pgvector": pgvector,
        }
    )

