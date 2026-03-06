"""API 健康状态追踪器（线程安全单例）

职责：
  - 收集每个 API 的调用成功/失败计数
  - 计算滑动窗口（5 分钟）内的错误趋势
  - 对外暴露 get_status()（永不抛错）和 get_recent_alerts()
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from .error_types import ErrorCategory, ApiCallRecord


@dataclass
class _ApiMetrics:
    """单个 API 方法的累计指标"""
    total_calls: int = 0
    success_count: int = 0
    error_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_error_time: Optional[datetime] = None
    last_error_msg: Optional[str] = None
    last_success_time: Optional[datetime] = None


class HealthTracker:
    """线程安全的单例健康追踪器

    使用两把锁分离职责：
      - _creation_lock: 类级，仅保护单例创建
      - _data_lock:     实例级，保护 _metrics / _recent_errors 读写
    """
    _instance: Optional[HealthTracker] = None
    _creation_lock = threading.Lock()

    def __new__(cls) -> HealthTracker:
        with cls._creation_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._data_lock = threading.Lock()
                inst._metrics: dict[str, _ApiMetrics] = defaultdict(_ApiMetrics)
                inst._recent_errors: list[ApiCallRecord] = []
                inst._max_recent = 50
                cls._instance = inst
        return cls._instance

    # ── 写入 ──────────────────────────────────

    def record(self, record: ApiCallRecord) -> None:
        with self._data_lock:
            m = self._metrics[record.api_name]
            m.total_calls += 1
            if record.success:
                m.success_count += 1
                m.last_success_time = record.timestamp
            else:
                cat_key = record.error_category.value if record.error_category else "unknown"
                m.error_counts[cat_key] += 1
                m.last_error_time = record.timestamp
                m.last_error_msg = record.msg
                self._recent_errors.append(record)
                if len(self._recent_errors) > self._max_recent:
                    self._recent_errors = self._recent_errors[-self._max_recent:]

    # ── 读取（永不抛错）──────────────────────

    def get_status(self) -> dict:
        """返回健康摘要。内部异常时降级为 degraded，绝不抛出。"""
        try:
            with self._data_lock:
                return self._compute_status()
        except Exception as e:
            logger.debug(f"健康检查内部错误: {e}")
            return {"status": "degraded", "reason": "health_check_internal_error"}

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        """返回最近的错误记录列表（最新在前）"""
        try:
            with self._data_lock:
                records = self._recent_errors[-limit:]
                return [
                    {
                        "api": r.api_name,
                        "category": r.error_category.value if r.error_category else "unknown",
                        "msg": r.msg,
                        "retry_count": r.retry_count,
                        "time": r.timestamp.isoformat(),
                    }
                    for r in reversed(records)
                ]
        except Exception as e:
            logger.debug(f"获取告警记录失败: {e}")
            return []

    def clear_auth_errors(self) -> None:
        """清除 auth_expired 类型的错误记录（用户更新 Cookie 后调用）"""
        with self._data_lock:
            self._recent_errors = [
                r for r in self._recent_errors
                if r.error_category != ErrorCategory.AUTH_EXPIRED
            ]

    def reset(self) -> None:
        """清空全部指标（仅测试用）"""
        with self._data_lock:
            self._metrics = defaultdict(_ApiMetrics)
            self._recent_errors = []

    # ── 内部计算 ─────────────────────────────

    def _compute_status(self) -> dict:
        if not self._metrics:
            return {"status": "healthy", "api_count": 0, "detail": {}}

        now = datetime.now()
        window = timedelta(minutes=5)
        recent = [r for r in self._recent_errors if r.timestamp > now - window]

        auth_errors = sum(
            1 for r in recent if r.error_category == ErrorCategory.AUTH_EXPIRED
        )
        rate_limit_errors = sum(
            1 for r in recent if r.error_category == ErrorCategory.RATE_LIMITED
        )
        total_recent = len(recent)

        # 状态判定：auth 失败 → unhealthy；软限流 ≥2 或近期 ≥3 错误 → degraded
        if auth_errors > 0:
            status = "unhealthy"
        elif rate_limit_errors >= 2:
            status = "degraded"
        elif total_recent >= 3:
            status = "degraded"
        else:
            status = "healthy"

        detail = {}
        for name, m in self._metrics.items():
            detail[name] = {
                "total": m.total_calls,
                "success": m.success_count,
                "errors": dict(m.error_counts),
                "last_error": m.last_error_msg,
            }

        return {
            "status": status,
            "auth_expired": auth_errors > 0,
            "rate_limited": rate_limit_errors > 0,
            "recent_errors_5min": total_recent,
            "detail": detail,
        }
