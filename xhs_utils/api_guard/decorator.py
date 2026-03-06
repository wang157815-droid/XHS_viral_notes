"""@guarded_api 装饰器 —— API 容错层核心

功能：
  1. 捕获异常并分类（兜底，正常情况下方法内部已 try/except）
  2. 根据 (success, msg, res_json) 三元组分类错误
  3. 按分类策略自动重试（瞬时 ≤2 次，限流 ≤1 次）
  4. 向 HealthTracker 汇报每次调用
  5. 永远返回 (success, msg, res_json) 三元组，不向上抛异常
"""

from __future__ import annotations

import time
import functools
from typing import Any, Callable

from loguru import logger

from .error_types import ErrorCategory, ApiCallRecord
from .classifier import classify_response, classify_exception
from .retry_policy import get_retry_policy
from .health import HealthTracker


def guarded_api(method: Callable) -> Callable:
    """装饰器，应用于所有直接调用小红书 API 的三元组接口。

    注意：被装饰的方法可能通过 run_in_executor 在线程池中执行，
    重试延迟（time.sleep）会占用该线程。延迟设计为极短（1-3s），
    不会耗尽默认线程池（32 线程），但长时间阻塞应交由业务层用
    asyncio.sleep 处理。
    """
    api_name = method.__name__

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> tuple:
        category = None
        last_msg: str | None = None
        retry_count = 0

        while True:
            # ── 调用原始方法 ──
            try:
                result = method(*args, **kwargs)
            except Exception as exc:
                category = classify_exception(exc)
                result = (False, f"[Guard] {type(exc).__name__}: {exc}", None)
                last_msg = str(exc)
            else:
                # ── 从三元组分类 ──
                if isinstance(result, tuple) and len(result) == 3:
                    success, msg, res_json = result
                    category = classify_response(success, msg, res_json)
                    last_msg = msg
                else:
                    category = None

            # ── 无错误 → 记录成功并返回 ──
            if category is None:
                HealthTracker().record(ApiCallRecord(
                    api_name=api_name, success=True, retry_count=retry_count,
                ))
                return result

            # ── 有错误 → 查询重试策略 ──
            policy = get_retry_policy(category)
            if retry_count < policy.max_retries:
                delay = policy.delay_for(retry_count)
                retry_count += 1
                logger.warning(
                    f"[Guard] {api_name} 错误({category.value})，"
                    f"第 {retry_count}/{policy.max_retries} 次重试，"
                    f"等待 {delay:.1f}s | msg={last_msg}"
                )
                time.sleep(delay)
                continue

            # ── 不再重试 → 记录失败并返回 ──
            if retry_count > 0:
                logger.warning(
                    f"[Guard] {api_name} 重试 {retry_count} 次后仍失败"
                    f"({category.value}) | msg={last_msg}"
                )
            HealthTracker().record(ApiCallRecord(
                api_name=api_name,
                success=False,
                error_category=category,
                msg=last_msg,
                retry_count=retry_count,
            ))
            return result

    return wrapper
