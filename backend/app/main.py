import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

load_dotenv()

from .api.router import api_router
from .core.config import settings
from .core.responses import ok
from .core.tracing import get_trace_id, reset_trace_id, set_trace_id
from .infrastructure.cache.redis_client import close_redis, get_redis
from .infrastructure.db import close_business_db_engine, ensure_business_schema
from .infrastructure.storage.db_engine import close_db_engine
from .services.redmuse_auth import bootstrap_admin_if_needed


def _reset_generating_modules() -> None:
    """重启时将所有卡在 GENERATING 状态的模块重置。

    有 content 的模块重置为 READY（保留上次结果），无 content 的重置为 STALE（等待重生）。
    只修改内存中的 canvas_document，并写回 task_context；不触发任何 SSE 事件。
    """
    from .domain.canvas.schema import CanvasSchema
    from .domain.module_status import ModuleStatus
    from .infrastructure.repository import task_repository
    from .domain.task_context import task_context_store

    try:
        records = task_repository.list_for_user("__system__", include_all=True, limit=9999)
    except Exception as exc:
        logger.warning("[startup] 获取任务列表失败，跳过 GENERATING 重置: {}", exc)
        return

    reset_count = 0
    for record in records:
        try:
            ctx = task_context_store.get(record.task_id)
            if ctx is None:
                continue
            canvas_doc = ctx.get("canvas_document")
            if not canvas_doc:
                continue
            canvas = CanvasSchema.from_dict(canvas_doc)
            changed = False
            for mod in canvas.modules:
                if mod.status == ModuleStatus.GENERATING:
                    has_content = bool(mod.content)
                    mod.status = ModuleStatus.READY if has_content else ModuleStatus.STALE
                    logger.info(
                        "[startup] 重置 GENERATING 模块 task={} module={} → {}",
                        record.task_id,
                        mod.module_id,
                        mod.status.value,
                    )
                    changed = True
                    reset_count += 1
            if changed:
                from .domain.task_context import task_context_store as _store
                w = _store.writer(record.task_id)
                w.write("canvas_document", canvas.to_dict(),
                        agent_id="startup", note="reset_generating_on_boot")
        except Exception as exc:
            logger.warning("[startup] 重置任务 {} 的 GENERATING 模块失败: {}", record.task_id, exc)

    if reset_count:
        logger.info("[startup] 共重置 {} 个卡住的 GENERATING 模块", reset_count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    skip_in_tests = os.getenv("REDMUSE_SKIP_STARTUP_CHECKS", "").lower() in ("1", "true", "yes")
    if not skip_in_tests:
        ensure_business_schema()
        await get_redis()
        # Phase 0: 首启动从 env 引导 admin 用户；已存在用户/无密码 env 时跳过。
        # 仅生产/dev 启动时执行；测试环境通过 REDMUSE_SKIP_STARTUP_CHECKS=true 跳过，
        # 避免污染真实 datas/redmuse_auth/users.json。
        bootstrap_admin_if_needed()
        # 重置上次进程崩溃/重启时卡住的 GENERATING 模块
        _reset_generating_modules()
    try:
        yield
    finally:
        await close_redis()
        await close_db_engine()
        close_business_db_engine()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="RedMuse refactor backend skeleton (phase0).",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    incoming = request.headers.get("x-request-id") or request.headers.get("x-trace-id")
    token = set_trace_id(incoming)
    trace_id = get_trace_id()
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response
    finally:
        reset_trace_id(token)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # noqa: ARG001
    trace_id = get_trace_id()
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error = dict(detail)
        error.setdefault("details", {})
        error["trace_id"] = error.get("trace_id") or trace_id
    else:
        error = {
            "code": f"HTTP_{exc.status_code}",
            "message": str(detail),
            "details": {},
            "trace_id": trace_id,
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": error},
        headers=getattr(exc, "headers", None),
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return ok(
        {
            "service": settings.app_name,
            "version": settings.app_version,
            "api_prefix": settings.api_prefix,
        }
    )

