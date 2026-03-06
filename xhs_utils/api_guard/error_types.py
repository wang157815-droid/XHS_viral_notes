"""API 错误分类枚举与结构化记录"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class ErrorCategory(Enum):
    """API 错误分类

    - TRANSIENT:       网络超时、连接中断等瞬时故障
    - RATE_LIMITED:    请求频率超限（服务端返回 429 或 msg 含限流关键词）
    - AUTH_EXPIRED:    Cookie / 会话过期
    - SIGNATURE_ERROR: JS 签名引擎异常
    - NOT_FOUND:       笔记已删除或资源不存在
    - UNKNOWN:         未匹配到任何已知模式
    """
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH_EXPIRED = "auth_expired"
    SIGNATURE_ERROR = "signature"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class SignatureError(Exception):
    """签名生成 / JS 编译失败时抛出"""
    pass


@dataclass
class ApiCallRecord:
    """单次 API 调用的结构化记录，供 HealthTracker 使用"""
    api_name: str
    success: bool
    error_category: Optional[ErrorCategory] = None
    msg: Optional[str] = None
    retry_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
