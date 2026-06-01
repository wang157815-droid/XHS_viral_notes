"""
统一错误码（5 大类）。

错误响应结构：
{
  "ok": false,
  "error": {"code", "message", "details", "trace_id"}
}
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from ..core.tracing import get_trace_id


class ErrorCategory(str, Enum):
    AUTH = "AUTH"
    INPUT = "INPUT"
    MODEL = "MODEL"
    CRAWLER = "CRAWLER"
    SYSTEM = "SYSTEM"


class ErrorCode(str, Enum):
    # AUTH_*
    AUTH_UNAUTHENTICATED = "AUTH_UNAUTHENTICATED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    AUTH_COOKIE_EXPIRED = "AUTH_COOKIE_EXPIRED"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    # Phase 2-B: 当前 RedMuse 用户尚未绑定 XHS 数据源 / 已过期。
    # 与 AUTH_COOKIE_EXPIRED 区别：那是泛指（含旧扫码 token）；本码专指
    # XhsCredentialStore 中 status=unbound/expired，前端可定向到「数据源授权」UI。
    AUTH_XHS_NOT_BOUND = "AUTH_XHS_NOT_BOUND"

    # INPUT_*
    INPUT_VALIDATION_FAILED = "INPUT_VALIDATION_FAILED"
    INPUT_IDEMPOTENCY_CONFLICT = "INPUT_IDEMPOTENCY_CONFLICT"
    INPUT_MODULE_VERSION_MISMATCH = "INPUT_MODULE_VERSION_MISMATCH"
    INPUT_MODULE_STATE_INVALID = "INPUT_MODULE_STATE_INVALID"
    INPUT_TASK_STATE_INVALID = "INPUT_TASK_STATE_INVALID"
    INPUT_NOT_FOUND = "INPUT_NOT_FOUND"

    # MODEL_*
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMIT = "MODEL_RATE_LIMIT"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_CONTENT_REJECTED = "MODEL_CONTENT_REJECTED"
    MODEL_UPSTREAM_ERROR = "MODEL_UPSTREAM_ERROR"
    MODEL_POLICY_MISSING = "MODEL_POLICY_MISSING"
    MODEL_PROVIDER_UNAVAILABLE = "MODEL_PROVIDER_UNAVAILABLE"
    MODEL_DEPENDENCY_MISSING = "MODEL_DEPENDENCY_MISSING"

    # CRAWLER_*
    CRAWLER_RATE_LIMITED = "CRAWLER_RATE_LIMITED"
    CRAWLER_XSEC_EXPIRED = "CRAWLER_XSEC_EXPIRED"
    CRAWLER_BLOCKED = "CRAWLER_BLOCKED"
    CRAWLER_CAPTCHA = "CRAWLER_CAPTCHA"
    CRAWLER_NOT_FOUND = "CRAWLER_NOT_FOUND"

    # SYSTEM_*
    SYSTEM_INTERNAL = "SYSTEM_INTERNAL"
    SYSTEM_DEPENDENCY = "SYSTEM_DEPENDENCY"
    SYSTEM_STORAGE = "SYSTEM_STORAGE"
    SYSTEM_DEGRADED = "SYSTEM_DEGRADED"


_HTTP_STATUS_BY_CODE: Dict[ErrorCode, int] = {
    ErrorCode.AUTH_UNAUTHENTICATED: 401,
    ErrorCode.AUTH_FORBIDDEN: 403,
    ErrorCode.AUTH_COOKIE_EXPIRED: 409,
    ErrorCode.AUTH_TOKEN_EXPIRED: 401,
    ErrorCode.AUTH_XHS_NOT_BOUND: 409,
    ErrorCode.INPUT_VALIDATION_FAILED: 400,
    ErrorCode.INPUT_IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INPUT_MODULE_VERSION_MISMATCH: 409,
    ErrorCode.INPUT_MODULE_STATE_INVALID: 409,
    ErrorCode.INPUT_TASK_STATE_INVALID: 409,
    ErrorCode.INPUT_NOT_FOUND: 404,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MODEL_RATE_LIMIT: 429,
    ErrorCode.MODEL_AUTH_FAILED: 502,
    ErrorCode.MODEL_CONTENT_REJECTED: 422,
    ErrorCode.MODEL_UPSTREAM_ERROR: 502,
    ErrorCode.MODEL_POLICY_MISSING: 500,
    ErrorCode.MODEL_PROVIDER_UNAVAILABLE: 503,
    ErrorCode.MODEL_DEPENDENCY_MISSING: 500,
    ErrorCode.CRAWLER_RATE_LIMITED: 429,
    ErrorCode.CRAWLER_XSEC_EXPIRED: 409,
    ErrorCode.CRAWLER_BLOCKED: 403,
    ErrorCode.CRAWLER_CAPTCHA: 403,
    ErrorCode.CRAWLER_NOT_FOUND: 404,
    ErrorCode.SYSTEM_INTERNAL: 500,
    ErrorCode.SYSTEM_DEPENDENCY: 500,
    ErrorCode.SYSTEM_STORAGE: 500,
    ErrorCode.SYSTEM_DEGRADED: 503,
}


def build_error(
    code: ErrorCode | str,
    message: str,
    *,
    details: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造统一错误响应 payload。"""
    code_value = code.value if isinstance(code, ErrorCode) else str(code)
    return {
        "ok": False,
        "error": {
            "code": code_value,
            "message": message,
            "details": details or {},
            "trace_id": trace_id or get_trace_id(uuid.uuid4().hex),
        },
    }


def raise_api_error(
    code: ErrorCode,
    message: str,
    *,
    details: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
) -> None:
    """以 HTTPException 形式抛出统一错误（FastAPI 会序列化 detail）。"""
    status_code = http_status or _HTTP_STATUS_BY_CODE.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    payload = build_error(code, message, details=details)
    raise HTTPException(status_code=status_code, detail=payload["error"])
