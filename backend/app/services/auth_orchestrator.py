from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from viral_agent.services.auth.qrcode_session import QRLoginStatus
from .identity_store import get_identity_store


class AuthOrchestrator:
    """
    新后端登录编排器（阶段1 + 阶段 4.1 hotfix 4）

    职责：
    1. 调用 Playwright 扫码服务创建/查询会话
    2. 在扫码成功后，使用 Cookie 拉取用户 selfinfo
    3. 生成系统内统一用户描述（user_id/nickname/role）

    hotfix 4 关键改动：
    - selfinfo 调用用 asyncio.to_thread 包装,避免阻塞 FastAPI 事件循环
    - session 级别缓存 user_profile, 每个 session 只调一次 selfinfo
      （之前前端每次轮询都重复调,在 cookie 刷新期可能触发 XHS 风控）

    注意：Playwright 需要 ProactorEventLoop（Windows），
    必须通过 backend/run.py 启动后端以确保事件循环正确。
    """

    def __init__(self) -> None:
        self.qrcode_service = None
        self.xhs_api = None
        self.identity_store = get_identity_store()
        # session_id → resolved user profile (只查一次,之后复用)
        self._profile_cache: Dict[str, Dict[str, Any]] = {}
        # 同一扫码会话只打一次关联日志（轮询会多次命中 SUCCESS）
        self._identity_link_logged: set[str] = set()

    def _get_qrcode_service(self):
        if self.qrcode_service is None:
            from viral_agent.services.auth import get_qrcode_login_service

            self.qrcode_service = get_qrcode_login_service()
        return self.qrcode_service

    def _get_xhs_api(self):
        if self.xhs_api is None:
            from apis.xhs_pc_apis import XHS_Apis

            self.xhs_api = XHS_Apis()
        return self.xhs_api

    async def create_qrcode_session(
        self,
        *,
        expected_user_id: Optional[str] = None,
        client_device_id: Optional[str] = None,
        creator_redmuse_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            from viral_agent.services.user_data_service import UserDataService

            bootstrap_username = UserDataService.DEFAULT_USER
        except Exception:
            bootstrap_username = "admin"

        qrcode_service = self._get_qrcode_service()
        session = await qrcode_service.create_session(
            bootstrap_username,
            expected_user_id=expected_user_id,
            client_device_id=client_device_id,
            creator_redmuse_user_id=creator_redmuse_user_id,
        )
        return {
            "session_id": session.session_id,
            "status": session.status.value,
            "expires_in": 180,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        }

    async def get_qrcode_session(
        self, session_id: str, *, link_with_previous: Optional[str] = None
    ) -> Dict[str, Any]:
        qrcode_service = self._get_qrcode_service()
        session = await qrcode_service.get_session(session_id)
        if not session:
            return {"exists": False}

        payload: Dict[str, Any] = {
            "exists": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "error_message": session.error_message,
            "is_active": session.is_active,
            "is_completed": session.is_completed,
            # 服务器模式返回的是整页截图，不是纯二维码。只要后台已经生成截图，
            # 前端就应该能展示；不要被状态切换（initializing/scanned/confirmed）清空。
            "qrcode_base64": session.qrcode_base64,
            "screenshot_version": getattr(session, "screenshot_version", 0),
            "cookies_ready": bool(session.cookies_str),
            "creator_redmuse_user_id": getattr(session, "creator_redmuse_user_id", None),
        }

        if session.status == QRLoginStatus.SUCCESS and session.cookies_str:
            # 命中 session 缓存时直接返回,避免每次轮询都调 selfinfo 阻塞事件循环
            cached = self._profile_cache.get(session.session_id)
            if cached is None:
                cached = await asyncio.to_thread(
                    self._extract_xhs_user_profile, session.cookies_str
                )
                self._profile_cache[session.session_id] = cached
            if not cached:
                payload["status"] = QRLoginStatus.ERROR.value
                payload["error_message"] = "Cookie 无法获取小红书用户身份，请清理后重新扫码"
                payload["user"] = None
                return payload
            # 即使走缓存也要处理关联：首轮请求可能尚未带 X-Redmuse-Previous-User-Id
            prev = (link_with_previous or "").strip() or None
            new_id = str((cached or {}).get("user_id") or "")
            if prev and new_id and prev != new_id:
                self.identity_store.link_user_ids(new_id, prev)
                if session.session_id not in self._identity_link_logged:
                    self._identity_link_logged.add(session.session_id)
                    logger.info(
                        f"已关联小红书身份: 当前 {new_id[:12]}… ↔ 上一账号 {prev[:12]}…（历史任务合并）"
                    )
            payload["user"] = cached

        return payload

    async def cancel_qrcode_session(self, session_id: str) -> bool:
        qrcode_service = self._get_qrcode_service()
        session = await qrcode_service.get_session(session_id)
        if not session:
            return False
        await qrcode_service.cancel_session(session_id)
        self._profile_cache.pop(session_id, None)
        self._identity_link_logged.discard(session_id)
        return True

    async def submit_sms_code(self, session_id: str, sms_code: str) -> bool:
        qrcode_service = self._get_qrcode_service()
        if not hasattr(qrcode_service, "submit_sms_code"):
            raise RuntimeError("当前扫码服务不支持短信验证码提交")
        return await qrcode_service.submit_sms_code(session_id, sms_code)

    async def get_session_cookies_str(self, session_id: str) -> Optional[str]:
        """Phase 2: 在 RedMuse 已登录的情况下，把扫码结果交给 binder 处理。"""
        qrcode_service = self._get_qrcode_service()
        session = await qrcode_service.get_session(session_id)
        if not session:
            return None
        if session.status != QRLoginStatus.SUCCESS:
            return None
        cookies_str = (session.cookies_str or "").strip()
        return cookies_str or None

    # 公共入口：Phase 2 起被 XhsCredentialBinder 调用，不再走下划线私有方法。
    def extract_xhs_identity_from_cookies(
        self, cookies_str: str
    ) -> Optional[Dict[str, Any]]:
        """同步：调 selfinfo → upsert IdentityStore → 落 cookies.json。

        返回结构：``{user_id, nickname, username, role, source, profile_synced_at}``，
        失败返回 ``None``（已记录 warning）。

        async 调用方应用 ``asyncio.to_thread`` 包装本方法，避免阻塞事件循环。
        """
        return self._extract_xhs_user_profile(cookies_str)

    def _extract_xhs_user_profile(self, cookies_str: str) -> Optional[Dict[str, Any]]:
        """
        从小红书 selfinfo 提取 user_id / nickname，并写入 IdentityStore。

        设计要点：
        - 优先接受 selfinfo 返回的历史兼容身份字段（短数字 ``result.data`` /
          ``basic_info.red_id``）+ nickname。
        - 不再把 selfinfo2 顶层 ``user_id`` 这种访客/临时对象 ID 当成系统登录身份，
          避免短信验证未完成或 guest Cookie 被解析成错误用户。
        """
        user_id, nickname, reason = self._pull_selfinfo_fields(cookies_str)

        if not user_id or not nickname:
            logger.warning(
                f"selfinfo 解析失败，拒绝使用 Cookie 登录 (原因: {reason or '结构缺失'})"
            )
            return None

        identity = self.identity_store.upsert_xhs_identity(user_id=user_id, nickname=nickname)
        self._save_cookie_for_identity(identity, cookies_str)
        return {
            "user_id": identity["user_id"],
            "nickname": identity["nickname"],
            "role": identity["role"],
            "username": identity["username"],
            "source": identity.get("source", "xhs_selfinfo"),
            "profile_synced_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _save_cookie_for_identity(identity: Dict[str, Any], cookies_str: str) -> None:
        username = str(identity.get("username") or "").strip()
        if not username:
            return
        try:
            from viral_agent.services.user_data_service import get_user_data_service

            user_data = get_user_data_service(username)
            if user_data.save_cookie(cookies_str):
                logger.info(f"Cookie 已保存到用户 {username} 的配置")
        except Exception as exc:
            logger.error(f"保存用户 {username} Cookie 失败: {exc}")

    def _pull_selfinfo_fields(self, cookies_str: str) -> tuple[str, str, Optional[str]]:
        """返回 (user_id, nickname, error_reason)。任一字段缺失时 error_reason 非空。"""
        try:
            xhs_api = self._get_xhs_api()
        except Exception as exc:
            logger.warning(f"XHS API 依赖未就绪: {exc}")
            return "", "", f"api_unready: {exc}"

        failures: list[str] = []
        for label, getter in (
            ("selfinfo", xhs_api.get_user_self_info),
            ("selfinfo2", xhs_api.get_user_self_info2),
        ):
            try:
                success, msg, data = getter(cookies_str)
            except Exception as exc:
                logger.warning(f"{label} 调用异常: {exc}")
                failures.append(f"{label}_call_exc: {exc}")
                continue

            if not success:
                logger.warning(f"{label} 拉取失败: {msg}")
                failures.append(f"{label}_failed: {msg}")
                continue

            user_id, nickname, source = self._extract_profile_fields((data or {}).get("data") or {})
            if user_id and nickname:
                logger.info(
                    f"{label} 解析到小红书身份: user_id={user_id[:12]}..., "
                    f"nickname={nickname}, source={source}"
                )
                return user_id, nickname, None
            if user_id and not nickname:
                failures.append(f"{label}_missing_nickname(user_id={user_id[:12]}...)")
                continue

            payload = (data or {}).get("data") or {}
            payload_keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            failures.append(f"{label}_missing_user_id(payload_keys={payload_keys})")

        return "", "", "; ".join(failures) if failures else "selfinfo_empty"

    @classmethod
    def _extract_profile_fields(cls, payload: Any) -> tuple[str, str, str]:
        """提取项目历史兼容的 XHS 身份 ID。

        旧数据里的 ``user_id`` 使用的是 selfinfo 里的短数字身份（通常来自
        ``result.data`` 或 ``basic_info.red_id``），而不是 selfinfo2 顶层
        ``user_id`` 这种 24 位访客/临时对象 ID。
        """
        if not isinstance(payload, dict):
            return "", "", "payload_not_dict"

        def _text(value: Any) -> str:
            return str(value).strip() if value is not None else ""

        def _fields(src: Any) -> tuple[str, str]:
            if not isinstance(src, dict):
                return "", ""
            uid = (
                src.get("user_id")
                or src.get("userId")
                or src.get("userID")
                or src.get("userid")
                or src.get("userIdStr")
            )
            name = src.get("nickname") or src.get("nick_name") or src.get("nickName")
            return _text(uid), _text(name)

        def _nickname() -> str:
            for src in (
                payload.get("basic_info"),
                payload.get("basicInfo"),
                payload.get("user_info"),
                payload.get("userInfo"),
                payload.get("result"),
                payload,
            ):
                _, name = _fields(src)
                if name:
                    return name
            return ""

        def _legacy_user_id() -> tuple[str, str]:
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            basic_info = payload.get("basic_info") if isinstance(payload.get("basic_info"), dict) else {}
            basic_info = basic_info or (
                payload.get("basicInfo") if isinstance(payload.get("basicInfo"), dict) else {}
            )
            candidates: tuple[tuple[str, Any], ...] = (
                ("result.data", result.get("data")),
                ("result.red_id", result.get("red_id")),
                ("result.redId", result.get("redId")),
                ("result.user_id", result.get("user_id")),
                ("result.userId", result.get("userId")),
                ("basic_info.red_id", basic_info.get("red_id")),
                ("basic_info.redId", basic_info.get("redId")),
                ("data.red_id", payload.get("red_id")),
                ("data.redId", payload.get("redId")),
            )
            for source, value in candidates:
                user_id = _text(value)
                if user_id and user_id.lower() not in ("true", "false", "none", "null"):
                    return user_id, source
            return "", ""

        legacy_user_id, legacy_source = _legacy_user_id()
        nickname = _nickname()
        if legacy_user_id and nickname:
            return legacy_user_id, nickname, legacy_source

        known_paths: tuple[tuple[str, ...], ...] = (
            ("user",),
            ("user_info",),
            ("userInfo",),
            ("basic_info",),
            ("basicInfo",),
            ("result",),
            ("result", "user"),
            ("result", "user_info"),
            ("result", "userInfo"),
            ("result", "basic_info"),
            ("result", "basicInfo"),
        )

        def _get_path(root: Dict[str, Any], path: tuple[str, ...]) -> Any:
            current: Any = root
            for key in path:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            return current

        for path in known_paths:
            candidate = _get_path(payload, path)
            user_id, nickname = _fields(candidate)
            if user_id and nickname:
                return user_id, nickname, ".".join(path) or "data"

        return "", "", "not_found"

    @staticmethod
    def _stable_fallback_user_id(cookies_str: str) -> str:
        digest = hashlib.sha256((cookies_str or "").encode("utf-8")).hexdigest()[:16]
        return f"fallback_{digest}"


auth_orchestrator = AuthOrchestrator()

