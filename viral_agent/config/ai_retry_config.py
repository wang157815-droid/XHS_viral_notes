"""AI 重试策略统一配置

所有 AI 分析模块（多模态、视频AI、帧ASR）共享此配置，
确保重试行为一致且可通过环境变量全局调整。
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AIRetryConfig:
    """AI 调用重试配置"""
    max_retries: int = 3
    base_delay: float = 5.0
    backoff_factor: float = 2.0
    timeout: float = 120.0
    retryable_status_codes: tuple = (429, 500, 502, 503, 504)
    retryable_error_patterns: tuple = ('429', '1302', 'rate limit', 'too many')

    @classmethod
    def from_env(cls) -> 'AIRetryConfig':
        """从环境变量加载配置，未设置时使用默认值"""
        return cls(
            max_retries=int(os.getenv('AI_MAX_RETRIES', '3')),
            base_delay=float(os.getenv('AI_RETRY_BASE_DELAY', '5.0')),
            timeout=float(os.getenv('AI_API_TIMEOUT', '120.0')),
        )

    def delay_for(self, attempt: int) -> float:
        """计算第 N 次重试的等待时间（指数退避）"""
        return self.base_delay * (self.backoff_factor ** attempt)

    def is_retryable_status(self, status_code: int) -> bool:
        """判断 HTTP 状态码是否可重试"""
        return status_code in self.retryable_status_codes

    def is_retryable_error(self, error_str: str) -> bool:
        """判断错误信息是否匹配可重试模式"""
        error_lower = error_str.lower()
        return any(p in error_lower for p in self.retryable_error_patterns)
