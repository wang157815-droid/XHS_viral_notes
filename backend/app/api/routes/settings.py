from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...core.responses import ok
from ...core.security import RoleLevel, get_current_user, normalize_role, role_allows
from ...llm import agent_model_policy, model_profile_registry, provider_registry
from ...services.cookie_health_service import cookie_health_service
from ...services.focus_keywords_store import get_focus_keywords_store
from ...services.identity_store import get_identity_store
from ...services.system_settings_store import get_system_settings_store

router = APIRouter(prefix="/settings", tags=["settings"])


def _require_admin(current_user: dict) -> None:
    if not role_allows(current_user.get("role"), RoleLevel.admin):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/system")
async def get_system_settings(current_user: dict = Depends(get_current_user)):
    _ = current_user
    data = await get_system_settings_store().get()
    return ok(data)


class CrawlerSchedulePayload(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(default=6, ge=1, le=168)
    hot_keywords_top_n: int = Field(default=50, ge=1, le=500)


class SystemSettingsPayload(BaseModel):
    text_model: str
    vision_model: str
    embedding_model: str
    video_analysis_enabled: bool = True
    crawler_schedule: CrawlerSchedulePayload = Field(default_factory=CrawlerSchedulePayload)


@router.put("/system")
async def update_system_settings(
    payload: SystemSettingsPayload,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    merged = await get_system_settings_store().replace(payload.model_dump())
    return ok(merged)


@router.put("/crawler-schedule")
async def update_crawler_schedule(
    payload: CrawlerSchedulePayload,
    current_user: dict = Depends(get_current_user),
):
    _ = current_user
    merged = await get_system_settings_store().update(
        {"crawler_schedule": payload.model_dump()}
    )
    return ok(merged)


@router.get("/cookie-health")
async def get_cookie_health(
    force: bool = False,
    current_user: dict = Depends(get_current_user),
):
    return ok(
        cookie_health_service.get_cookie_health(
            current_user=current_user,
            force_check=force,
        )
    )


@router.get("/focus-keywords")
async def get_focus_keywords(current_user: dict = Depends(get_current_user)):
    _ = current_user
    items = await get_focus_keywords_store().get()
    return ok({"items": items})


class FocusKeywordsPayload(BaseModel):
    items: list[str] = Field(default_factory=list)


@router.put("/focus-keywords")
async def update_focus_keywords(
    payload: FocusKeywordsPayload,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    normalized = await get_focus_keywords_store().replace(payload.items)
    return ok({"items": normalized})


@router.get("/model-governance")
async def get_model_governance(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    providers = []
    for name in provider_registry.list_names():
        cfg = provider_registry.get(name)
        if not cfg:
            continue
        providers.append(
            {
                "name": cfg.name,
                "base_url": cfg.base_url,
                "kind": cfg.kind,
                "api_key_configured": bool(cfg.api_key),
                "usable": cfg.is_usable(),
            }
        )

    profiles = []
    for pid, profile in model_profile_registry.list_profiles().items():
        profiles.append(
            {
                "profile_id": pid,
                "provider": profile.provider,
                "model_name": profile.model_name,
                "modality": profile.modality,
                "temperature": profile.temperature,
                "max_tokens": profile.max_tokens,
                "timeout_seconds": profile.timeout_seconds,
                "max_retries": profile.max_retries,
            }
        )

    policy = []
    for entry in agent_model_policy.list_entries().values():
        policy.append(
            {
                "agent_id": entry.agent_id,
                "modality": entry.modality,
                "profile_id": entry.profile_id,
            }
        )

    return ok({"providers": providers, "profiles": profiles, "policy": policy})


class AgentPolicyUpdate(BaseModel):
    agent_id: str
    modality: str
    profile_id: str


@router.put("/model-governance/policy")
async def update_agent_policy(
    payload: AgentPolicyUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    if payload.profile_id not in model_profile_registry.list_profiles():
        raise HTTPException(status_code=400, detail=f"未知 profile: {payload.profile_id}")
    agent_model_policy.set(payload.agent_id, payload.modality, payload.profile_id)
    return ok(
        {
            "agent_id": payload.agent_id,
            "modality": payload.modality,
            "profile_id": payload.profile_id,
        }
    )


# ---------------------------------------------------------------------------
# 定时爬虫状态与手动触发（阶段 4.α 接入 ARQ 真实任务）
# ---------------------------------------------------------------------------
@router.get("/crawler-status")
async def get_crawler_status(current_user: dict = Depends(get_current_user)):
    _ = current_user
    # 真实读 Redis 里最近一次预热的结果 + pgvector 里累积的笔记数
    last_run_payload: Dict[str, Any] = {
        "status": "never_run",
        "at": None,
        "collected_count": 0,
    }
    total_records = 0
    keyword_count = 0

    try:
        from ...infrastructure.queue.tasks.warmup import read_last_run

        last_run = await read_last_run()
        if last_run:
            last_run_payload = {
                "status": last_run.get("status", "unknown"),
                "at": last_run.get("finished_at") or last_run.get("started_at"),
                "collected_count": sum(
                    int(item.get("count", 0) or 0)
                    for item in (last_run.get("per_keyword") or [])
                ),
                "ok_count": last_run.get("ok_count", 0),
                "failed_count": last_run.get("failed_count", 0),
            }
    except Exception:  # noqa: BLE001
        pass

    try:
        from ...infrastructure.storage.db_engine import get_db_engine
        from sqlalchemy import text as _sql

        engine = await get_db_engine()
        async with engine.connect() as conn:
            r = await conn.execute(_sql("SELECT COUNT(*) FROM xhs_notes"))
            total_records = int(r.scalar() or 0)
            r = await conn.execute(
                _sql(
                    "SELECT COUNT(DISTINCT kw) FROM xhs_notes, "
                    "LATERAL UNNEST(source_keywords) AS kw"
                )
            )
            keyword_count = int(r.scalar() or 0)
    except Exception:  # noqa: BLE001
        # pgvector 未就绪时保持 0,不报错（面板可显示 "N/A"）
        pass

    return ok(
        {
            "last_run": last_run_payload,
            "total_records": total_records,
            "keyword_count": keyword_count,
        }
    )


@router.post("/crawler/run-now")
async def trigger_crawler_now(current_user: dict = Depends(get_current_user)):
    """用户点"立即采集"触发,force=True 穿透 interval 节流（仍尊重开关）。"""
    _ = current_user
    triggered_at = datetime.now(timezone.utc).isoformat()
    try:
        from ...infrastructure.queue.client import enqueue_job

        # force=True: 用户意图明确,不受 interval 节流限制
        job_id = await enqueue_job("scheduled_warmup", force=True)
        if job_id:
            return ok(
                {
                    "triggered_at": triggered_at,
                    "job_id": job_id,
                    "message": "已下发到 ARQ 队列,请稍后在此页查看预热结果",
                }
            )
    except Exception:  # noqa: BLE001
        pass

    # ARQ 未就绪：直接在本进程 fire-and-forget 跑一次（简单场景下够用）
    import asyncio as _asyncio

    async def _inprocess_run():
        from ...infrastructure.queue.tasks.warmup import scheduled_warmup

        try:
            await scheduled_warmup({}, force=True)
        except Exception:
            pass

    _asyncio.create_task(_inprocess_run())
    return ok(
        {
            "triggered_at": triggered_at,
            "message": "已在本进程后台异步执行（ARQ 未就绪）",
        }
    )


# ---------------------------------------------------------------------------
# 用户管理（管理员可见）
# ---------------------------------------------------------------------------
def _mask_xhs_id(raw: str) -> str:
    if not raw:
        return ""
    if len(raw) <= 8:
        return raw
    return f"{raw[:8]}...{raw[-4:]}"


@router.get("/users")
async def list_users(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    store = get_identity_store()
    items = store.list_identities()
    return ok(
        {
            "items": [
                {
                    "user_id": i.get("user_id"),
                    "nickname": i.get("nickname"),
                    "xhs_id_masked": _mask_xhs_id(str(i.get("user_id", ""))),
                    "username": i.get("username"),
                    "role": normalize_role(i.get("role", "analyst")),
                    "source": i.get("source"),
                    "last_login_at": i.get("profile_synced_at"),
                }
                for i in items
            ]
        }
    )


class UserUpdatePayload(BaseModel):
    role: str


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: UserUpdatePayload,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    normalized_role = normalize_role(payload.role)
    if normalized_role not in ("admin", "analyst", "viewer"):
        raise HTTPException(status_code=400, detail="非法角色")
    store = get_identity_store()
    updated = store.set_role(user_id, normalized_role)
    if not updated:
        raise HTTPException(status_code=404, detail="用户不存在")
    return ok(updated)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    if user_id == current_user.get("user_id"):
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    store = get_identity_store()
    deleted = store.delete(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="用户不存在")
    return ok({"user_id": user_id, "deleted": True})


@router.get("/maintenance")
async def get_maintenance_stats(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from ...infrastructure.queue.tasks.cleanup import get_maintenance_stats_snapshot

    return ok(get_maintenance_stats_snapshot())


class MaintenanceCleanPayload(BaseModel):
    target: str


@router.post("/maintenance/clean")
async def maintenance_clean(
    payload: MaintenanceCleanPayload,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    if payload.target not in ("video_cache", "analysis_cache", "browser_data"):
        raise HTTPException(status_code=400, detail="非法清理目标")

    from ...infrastructure.queue.tasks.cleanup import clean_cache_target

    stats = await clean_cache_target(payload.target, retention_days=0)
    return ok(
        {
            "target": payload.target,
            "cleaned": True,
            "freed_bytes": stats.get("deleted_bytes", 0),
            "deleted_count": stats.get("deleted_count", 0),
            "message": f"已清理 {payload.target}: 释放 {stats.get('deleted_bytes', 0) // 1024} KB",
            "details": stats.get("details", []),
        }
    )

