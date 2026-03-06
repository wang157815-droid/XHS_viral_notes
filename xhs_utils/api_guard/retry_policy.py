"""每种 ErrorCategory 对应的重试策略

Guard 层与业务层（viral_collector）的职责边界：
  ┌──────────────┬──────────────────────────┬────────────────────────────┐
  │ 场景         │ Guard 层                 │ 业务层                      │
  ├──────────────┼──────────────────────────┼────────────────────────────┤
  │ 硬失败       │ classify → 按策略重试    │ 看到 success=False → break │
  │ (success=F)  │ TRANSIENT ≤2, RATE ≤1   │ 不再额外重试               │
  ├──────────────┼──────────────────────────┼────────────────────────────┤
  │ 软限流       │ classify 返回 None       │ 检测 items=[] → 自行重试   │
  │ (success=T,  │ （不介入，避免叠加）      │ + 自适应降速               │
  │  items=[])   │                          │                            │
  └──────────────┴──────────────────────────┴────────────────────────────┘

约定：
  - 瞬时错误（TRANSIENT）: 最多重试 2 次，指数退避（1s → 2s）
  - 限流（RATE_LIMITED）:   最多重试 1 次，固定延迟 3s
  - 其余类别：不重试（auth 过期 / 签名错误重试无意义）
"""

from __future__ import annotations

from dataclasses import dataclass

from .error_types import ErrorCategory


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    base_delay: float          # 首次重试延迟（秒）
    backoff_factor: float = 1.0  # 延迟倍增系数

    def delay_for(self, attempt: int) -> float:
        """第 attempt 次重试应等待的秒数（attempt 从 0 开始）"""
        return self.base_delay * (self.backoff_factor ** attempt)


_NO_RETRY = RetryPolicy(max_retries=0, base_delay=0)

RETRY_POLICIES: dict[ErrorCategory, RetryPolicy] = {
    ErrorCategory.TRANSIENT:       RetryPolicy(max_retries=2, base_delay=1.0, backoff_factor=2.0),
    ErrorCategory.RATE_LIMITED:    RetryPolicy(max_retries=1, base_delay=3.0),
    ErrorCategory.AUTH_EXPIRED:    _NO_RETRY,
    ErrorCategory.SIGNATURE_ERROR: _NO_RETRY,
    ErrorCategory.NOT_FOUND:       _NO_RETRY,
    ErrorCategory.UNKNOWN:         _NO_RETRY,
}


def get_retry_policy(category: ErrorCategory) -> RetryPolicy:
    return RETRY_POLICIES.get(category, _NO_RETRY)
