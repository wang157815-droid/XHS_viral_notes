"""XHS 凭据健康检查（Phase 1 占位 + 委托）。

Phase 1 范围：
- 提供一个轻量的 ``check(redmuse_user_id)`` 入口，对外返回结构化结果。
- 实际的"调用 selfinfo 接口校验 cookie 是否仍然有效"逻辑，复用既有
  :class:`backend.app.services.cookie_health_service.CookieHealthService`，
  避免重复实现。本类只负责把结果写回 :class:`XhsCredentialStore`，让
  store 中的 ``status`` 字段成为单一真相来源。

Phase 2 起会扩展为：
- 节流 / 缓存 / 异步刷新；
- 失败时通知 XhsAuthAgent 触发重新授权流程。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .credential_store import XhsCredentialStore, get_credential_store


REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class XhsCredentialHealth:
    redmuse_user_id: str
    status: str  # active | expired | expiring_soon | unknown | unbound
    message: str
    cookies_path: Optional[str]
    last_validated_at: Optional[str]
    is_bound: bool


class XhsCredentialHealthChecker:
    """委托 :class:`CookieHealthService` 的薄封装。"""

    EXPIRING_SOON_DAYS = 5

    def __init__(self, store: Optional[XhsCredentialStore] = None) -> None:
        self._store = store

    @property
    def store(self) -> XhsCredentialStore:
        return self._store or get_credential_store()

    def check(self, redmuse_user_id: str, *, force: bool = False) -> XhsCredentialHealth:
        credential = self.store.get_by_redmuse_user_id(redmuse_user_id)
        if not credential or not credential.cookies_path:
            return XhsCredentialHealth(
                redmuse_user_id=redmuse_user_id,
                status="unbound",
                message="未绑定小红书账号，请先扫码授权",
                cookies_path=None,
                last_validated_at=None,
                is_bound=False,
            )

        # 直接复用 cookie_health_service 的判定逻辑，传入 cookies_path 对应目录名。
        # 由于 CookieHealthService 当前以 username 作为目录名，这里把 cookies_path
        # 中的目录段抽出来当 username 用，保持兼容。
        username = self._extract_username_from_path(credential.cookies_path)

        try:
            from ..cookie_health_service import cookie_health_service

            payload = cookie_health_service.get_cookie_health(
                current_user={"username": username},
                force_check=force,
            )
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning(f"[xhs_auth] 健康检查异常: {exc}")
            payload = {
                "status": "unknown",
                "message": f"健康检查异常: {exc}",
                "last_checked_at": None,
            }

        status = str(payload.get("status") or "unknown")
        message = str(payload.get("message") or "")
        last_checked = (
            str(payload["last_checked_at"]) if payload.get("last_checked_at") else None
        )

        # 写回 store 让其它读取方有一致视图
        self.store.update_status(
            redmuse_user_id,
            status=status,
            status_message=message,
            last_validated_at=last_checked or _now_iso(),
        )

        return XhsCredentialHealth(
            redmuse_user_id=redmuse_user_id,
            status=status,
            message=message,
            cookies_path=credential.cookies_path,
            last_validated_at=last_checked,
            is_bound=True,
        )

    @staticmethod
    def _extract_username_from_path(cookies_path: str) -> str:
        """``datas/users/<username>/cookies.json`` → ``<username>``。"""
        try:
            parts = Path(cookies_path).parts
            if "users" in parts:
                idx = parts.index("users")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
        except Exception:
            pass
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_default_checker: Optional[XhsCredentialHealthChecker] = None
_default_checker_lock = threading.Lock()


def get_credential_health_checker() -> XhsCredentialHealthChecker:
    global _default_checker
    if _default_checker is None:
        with _default_checker_lock:
            if _default_checker is None:
                _default_checker = XhsCredentialHealthChecker()
    return _default_checker
