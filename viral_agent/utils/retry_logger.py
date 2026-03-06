"""AI 重试统一日志

所有 AI 分析模块共享日志格式，便于在日志中快速搜索 [AI重试] 前缀。
"""

from loguru import logger


def log_ai_retry(
    component: str,
    error_type: str,
    attempt: int,
    max_retries: int,
    wait_seconds: float
) -> None:
    """记录 AI 重试"""
    logger.warning(
        f"[AI重试] {component} | {error_type} | "
        f"第 {attempt}/{max_retries} 次重试，等待 {wait_seconds:.0f}s"
    )


def log_ai_retry_exhausted(
    component: str,
    error_type: str,
    max_retries: int
) -> None:
    """记录 AI 重试耗尽"""
    logger.error(
        f"[AI重试] {component} | {error_type} | "
        f"已重试 {max_retries} 次仍失败"
    )


def log_ai_fallback(
    component: str,
    fallback_to: str,
    reason: str
) -> None:
    """记录 AI 降级"""
    logger.warning(
        f"[AI重试] {component} | 降级到 {fallback_to} | 原因: {reason}"
    )
