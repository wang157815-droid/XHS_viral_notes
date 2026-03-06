"""API Guard —— 小红书接口容错层

提供错误分类、自动重试、健康追踪三位一体的防护。
覆盖所有直接调用小红书 API 的三元组方法。
"""

from .error_types import ErrorCategory, SignatureError, ApiCallRecord
from .classifier import classify_response, classify_exception
from .retry_policy import RetryPolicy, get_retry_policy
from .decorator import guarded_api
from .health import HealthTracker

__all__ = [
    "ErrorCategory",
    "SignatureError",
    "ApiCallRecord",
    "classify_response",
    "classify_exception",
    "RetryPolicy",
    "get_retry_policy",
    "guarded_api",
    "HealthTracker",
]
