"""根据 API 返回的 success / msg / res_json 或捕获的异常，判定错误分类"""

from __future__ import annotations

from typing import Optional

from .error_types import ErrorCategory, SignatureError

# ── 关键词表 ────────────────────────────────────

_AUTH_KEYWORDS = [
    "请登录", "未登录", "need login", "session expired",
    "登录过期", "请先登录",
]

_RATE_LIMIT_KEYWORDS = [
    "频繁", "too many", "rate limit", "请稍后", "操作过快",
]

_NOT_FOUND_KEYWORDS = [
    "不存在", "已删除", "not found", "笔记已删除", "该内容已",
]

_TRANSIENT_KEYWORDS = [
    "timed out", "timeout", "connection",
    "expecting value", "jsondecode", "remotedisconnected",
    "connectionreset", "brokenpipe", "chunkedencodingerror",
]


# ── 公开接口 ────────────────────────────────────

def classify_response(
    success: bool,
    msg: Optional[str],
    res_json: Optional[dict],
    status_code: Optional[int] = None,
) -> Optional[ErrorCategory]:
    """根据 API 三元组返回值分类错误。返回 None 表示无错误。"""
    # 1) HTTP 状态码（可选字段，优先级最高）
    if status_code is not None:
        if status_code == 429:
            return ErrorCategory.RATE_LIMITED
        if status_code in (401, 403):
            return ErrorCategory.AUTH_EXPIRED
        if status_code >= 500:
            return ErrorCategory.TRANSIENT

    # 2) success=True → 视为正常（软限流由业务层判断）
    if success:
        return None

    # 3) success=False → 按 msg 关键词匹配
    msg_lower = (msg or "").lower()
    if _match(msg_lower, _AUTH_KEYWORDS):
        return ErrorCategory.AUTH_EXPIRED
    if _match(msg_lower, _RATE_LIMIT_KEYWORDS):
        return ErrorCategory.RATE_LIMITED
    if _match(msg_lower, _NOT_FOUND_KEYWORDS):
        return ErrorCategory.NOT_FOUND
    if _match(msg_lower, _TRANSIENT_KEYWORDS):
        return ErrorCategory.TRANSIENT

    return ErrorCategory.UNKNOWN


def classify_exception(exc: Exception) -> ErrorCategory:
    """根据异常类型分类错误（用于 Guard 层兜底 catch）"""
    if isinstance(exc, SignatureError):
        return ErrorCategory.SIGNATURE_ERROR

    # requests 库异常
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return ErrorCategory.TRANSIENT
        if isinstance(exc, requests.exceptions.ChunkedEncodingError):
            return ErrorCategory.TRANSIENT
    except ImportError:
        pass

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return ErrorCategory.TRANSIENT

    # Cookie 缺失（ValueError from generate_request_params）
    if isinstance(exc, ValueError) and "a1" in str(exc):
        return ErrorCategory.AUTH_EXPIRED

    # execjs 运行时错误
    exc_module = getattr(type(exc), "__module__", "") or ""
    if "execjs" in exc_module:
        return ErrorCategory.SIGNATURE_ERROR

    # 兜底：从异常文本推断
    msg_lower = str(exc).lower()
    if _match(msg_lower, _TRANSIENT_KEYWORDS):
        return ErrorCategory.TRANSIENT

    return ErrorCategory.UNKNOWN


# ── 内部工具 ────────────────────────────────────

def _match(text: str, keywords: list[str]) -> bool:
    return any(kw.lower() in text for kw in keywords)
