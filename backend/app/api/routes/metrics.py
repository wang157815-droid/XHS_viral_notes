"""Admin observability APIs for phase 4.7."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...core.responses import ok
from ...core.security import require_admin_user
from ...infrastructure.db import metrics_store

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/summary")
async def get_metrics_summary(
    window_hours: int = Query(24, ge=1, le=24 * 30),
    current_user: dict = Depends(require_admin_user),
):
    _ = current_user
    return ok(metrics_store.get_summary(window_hours=window_hours))
