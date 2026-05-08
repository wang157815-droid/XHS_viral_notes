"""把 cookies_str 绑定到当前 RedMuse 用户（Phase 2-A）。

绑定动作 = "扫码登录小红书 + 落 cookies.json + 写 XhsCredentialStore"。

调用顺序：

1. ``AuthOrchestrator.extract_xhs_identity_from_cookies`` 拉 selfinfo →
   upsert IdentityStore → 落 ``datas/users/<xhs_username>/cookies.json``
2. 在 ``XhsCredentialStore`` 写一条 ``redmuse_user_id → cookies_path`` 的记录，
   状态置为 ``active``。

之所以单独抽一层 binder 而不是把逻辑塞回 AuthOrchestrator：
- AuthOrchestrator 仍要服务旧扫码登录路径（不含 RedMuse user 的场景），
- Binder 持有的"为某 RedMuse 用户绑定"的语义更清晰，便于后续 XhsAuthAgent 复用。

返回结构均为 :class:`BindResult`，含 ``status``（success / failed）、错误原因、
公开 credential payload。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from .credential_store import XhsCredentialStore, get_credential_store


@dataclass
class BindResult:
    success: bool
    redmuse_user_id: str
    xhs_user_id: Optional[str] = None
    xhs_nickname: Optional[str] = None
    cookies_path: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    credential_public: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        if self.success:
            return {
                "success": True,
                "redmuse_user_id": self.redmuse_user_id,
                "xhs_user_id": self.xhs_user_id,
                "xhs_nickname": self.xhs_nickname,
                "credential": self.credential_public,
            }
        return {
            "success": False,
            "redmuse_user_id": self.redmuse_user_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class XhsCredentialBinder:
    """单例（默认）；测试可注入隔离的 store / orchestrator。"""

    def __init__(
        self,
        store: Optional[XhsCredentialStore] = None,
        orchestrator: Any = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator

    @property
    def store(self) -> XhsCredentialStore:
        return self._store or get_credential_store()

    @property
    def orchestrator(self) -> Any:
        if self._orchestrator is None:
            from ..auth_orchestrator import auth_orchestrator

            self._orchestrator = auth_orchestrator
        return self._orchestrator

    # ------------------------------------------------------------------
    # 主流程：cookies_str → identity → store
    # ------------------------------------------------------------------
    async def bind_with_cookies(
        self, redmuse_user_id: str, cookies_str: str
    ) -> BindResult:
        """直接给一段 ``cookies_str`` 完成绑定。

        前端"粘贴 Cookie"调试入口、后端测试都用这个；扫码场景的入口是
        :meth:`bind_from_qr_session`，最终也回到这里。
        """
        rm_uid = (redmuse_user_id or "").strip()
        if not rm_uid:
            return BindResult(
                success=False,
                redmuse_user_id="",
                error_code="invalid_redmuse_user",
                error_message="redmuse_user_id 缺失",
            )
        cookies = (cookies_str or "").strip()
        if not cookies:
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="empty_cookies",
                error_message="cookies_str 为空",
            )

        try:
            identity = await asyncio.to_thread(
                self.orchestrator.extract_xhs_identity_from_cookies, cookies
            )
        except Exception as exc:
            logger.exception(f"[xhs_auth] selfinfo 调用异常: {exc}")
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="selfinfo_error",
                error_message=f"selfinfo 调用失败: {exc}",
            )

        if not identity:
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="selfinfo_invalid",
                error_message="Cookie 无效或无法解析小红书身份，请重新扫码",
            )

        xhs_username = str(identity.get("username") or "").strip()
        xhs_user_id = str(identity.get("user_id") or "").strip()
        xhs_nickname = str(identity.get("nickname") or "").strip() or None

        if not xhs_username or not xhs_user_id:
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="identity_incomplete",
                error_message="小红书身份字段不完整",
            )

        cookies_path = f"datas/users/{xhs_username}/cookies.json"

        # 将该 RedMuse 用户和已存在的同 xhs_user_id 记录冲突时，沿用同一份 cookies.json
        # 但状态归属更新到当前 RedMuse 用户。
        cred = self.store.upsert(
            redmuse_user_id=rm_uid,
            cookies_path=cookies_path,
            xhs_user_id=xhs_user_id,
            xhs_nickname=xhs_nickname,
            status="active",
            status_message="cookies 绑定成功",
            last_validated_at=_now_iso(),
        )

        logger.info(
            f"[xhs_auth] RedMuse 用户 {rm_uid} 已绑定 XHS 账号 "
            f"({xhs_user_id[:12]}…/{xhs_nickname}) → {cookies_path}"
        )

        return BindResult(
            success=True,
            redmuse_user_id=rm_uid,
            xhs_user_id=xhs_user_id,
            xhs_nickname=xhs_nickname,
            cookies_path=cookies_path,
            credential_public=cred.public_dict(),
        )

    async def bind_from_qr_session(
        self, redmuse_user_id: str, session_id: str
    ) -> BindResult:
        """扫码完成后把结果绑到当前 RedMuse 用户。"""
        rm_uid = (redmuse_user_id or "").strip()
        sid = (session_id or "").strip()
        if not rm_uid or not sid:
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="invalid_arguments",
                error_message="redmuse_user_id 或 session_id 缺失",
            )

        try:
            cookies = await self.orchestrator.get_session_cookies_str(sid)
        except Exception as exc:
            logger.exception(f"[xhs_auth] 读取扫码 session cookies 异常: {exc}")
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="session_error",
                error_message=f"读取扫码会话失败: {exc}",
            )

        if not cookies:
            return BindResult(
                success=False,
                redmuse_user_id=rm_uid,
                error_code="session_not_ready",
                error_message="扫码会话尚未完成或已过期",
            )

        return await self.bind_with_cookies(rm_uid, cookies)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_default_binder: Optional[XhsCredentialBinder] = None
_default_binder_lock = threading.Lock()


def get_credential_binder() -> XhsCredentialBinder:
    global _default_binder
    if _default_binder is None:
        with _default_binder_lock:
            if _default_binder is None:
                _default_binder = XhsCredentialBinder()
    return _default_binder
