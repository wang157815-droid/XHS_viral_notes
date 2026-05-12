import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from .api.router import api_router
from .core.config import settings
from .core.responses import ok
from .core.tracing import get_trace_id, reset_trace_id, set_trace_id
from .infrastructure.cache.redis_client import close_redis, get_redis
from .infrastructure.db import close_business_db_engine, ensure_business_schema
from .infrastructure.storage.db_engine import close_db_engine
from .services.redmuse_auth import bootstrap_admin_if_needed


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

