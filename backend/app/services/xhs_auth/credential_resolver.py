"""把"RedMuse 用户 user_id"翻译成"可发起 XHS 请求的 Cookie 字符串"。

调用方（CrawlerAgent / cookie_health_service / 其它需要 Cookie 的代码）唯一
入口，避免重复实现回退逻辑。

解析顺序（Phase 1 + LiveCookie 增强）：

0. :class:`LiveCookieProvider`（活浏览器实时取）— 仅当 ``LIVE_COOKIE_ENABLED=true``
   且能找到 username/cookies_path 时生效；失败时静默跌落至下一步。
1. ``XHS_COOKIES_OVERRIDE`` 环境变量（调试 / 应急）
2. :class:`XhsCredentialStore` 中以 ``redmuse_user_id`` 命中的记录
   → 读取 ``cookies_path`` 指向的 ``cookies.json``
3. 兼容老路径（仅当 owner 看上去像 XHS user_id 而不是 RedMuse user_id 时启用）
   → ``identity_store`` 查 username → ``datas/users/<username>/cookies.json``
4. ``ALLOW_ADMIN_COOKIE_FALLBACK=true`` 时回退 ``datas/users/admin/cookies.json``
5. ``COOKIES`` / ``COOKIE`` 环境变量（兼容旧 viral_app .env）

注意：
- 第 0 步（LiveCookie）只在 ``LIVE_COOKIE_ENABLED=true`` 且 browser_data/<username>
  存在时生效；Playwright 不可用或 browser_data 未建立时会静默跌落，不影响已有逻辑。
- 第 3 步只是为了让 Phase 1 不破坏既有任务（旧任务 ``owner_user_id`` 是 XHS
  数字 ID，identity_store 仍能找到），但默认 emit 一条 deprecation log。
- 第 4 步默认关闭，避免多 RedMuse 用户互相串号。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from .credential_store import XhsCredentialStore, get_credential_store


REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class ResolvedCookie:
    """一次解析的结果，含来源说明便于排查。"""

    cookies_str: str
    source: str  # "live_browser" | "credential" | "legacy_username" | "admin_fallback" | "env_override" | "env_compat"
    cookies_path: Optional[str] = None
    redmuse_user_id: Optional[str] = None
    xhs_user_id: Optional[str] = None

    @property
    def found(self) -> bool:
        return bool(self.cookies_str)


def _looks_like_redmuse_user_id(value: str) -> bool:
    return bool(value) and value.startswith("u_")


def _read_cookie_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Cookie 文件解析失败: {path} ({exc})")
        return None
    cookie = payload.get("cookie")
    if isinstance(cookie, str) and cookie.strip():
        return cookie
    return None


def _resolve_path(cookies_path: str) -> Path:
    p = Path(cookies_path)
    if p.is_absolute():
        return p
    return REPO_ROOT / p


class XhsCredentialResolver:
    """根据 ``redmuse_user_id`` 找到合适的 Cookie 字符串。"""

    def __init__(self, store: Optional[XhsCredentialStore] = None) -> None:
        self._store = store

    @property
    def store(self) -> XhsCredentialStore:
        return self._store or get_credential_store()

    def resolve(self, owner_user_id: Optional[str]) -> ResolvedCookie:
        # 0. 活浏览器实时取（LIVE_COOKIE_ENABLED=true 时优先）
        live_result = self._try_live_cookie(owner_user_id)
        if live_result is not None:
            return live_result

        # 1. 调试 / 应急覆盖
        override = (os.getenv("XHS_COOKIES_OVERRIDE") or "").strip()
        if override:
            return ResolvedCookie(cookies_str=override, source="env_override")

        owner = (owner_user_id or "").strip()

        # 2. RedMuse 用户路径：通过 XhsCredentialStore 查
        if owner and _looks_like_redmuse_user_id(owner):
            credential = self.store.get_by_redmuse_user_id(owner)
            if credential and credential.cookies_path:
                cookie = _read_cookie_file(_resolve_path(credential.cookies_path))
                if cookie:
                    return ResolvedCookie(
                        cookies_str=cookie,
                        source="credential",
                        cookies_path=credential.cookies_path,
                        redmuse_user_id=owner,
                        xhs_user_id=credential.xhs_user_id,
                    )

        # 3. 兼容旧任务：owner 看起来是 XHS 数字 ID，从 identity_store 反查 username
        if owner and not _looks_like_redmuse_user_id(owner):
            legacy_cookie, legacy_path = self._lookup_legacy_username_cookie(owner)
            if legacy_cookie:
                logger.warning(
                    "[xhs_auth] 老路径解析 cookie 命中（owner=%s, path=%s）；"
                    "建议将该任务的 owner_user_id 迁移成 RedMuse user_id 并写入 XhsCredentialStore。",
                    owner,
                    legacy_path,
                )
                return ResolvedCookie(
                    cookies_str=legacy_cookie,
                    source="legacy_username",
                    cookies_path=legacy_path,
                )

        # 4. admin 回退（默认关闭）
        if _admin_fallback_enabled():
            admin_path = REPO_ROOT / "datas" / "users" / "admin" / "cookies.json"
            cookie = _read_cookie_file(admin_path)
            if cookie:
                logger.info(
                    "[xhs_auth] 回退到 admin cookie（ALLOW_ADMIN_COOKIE_FALLBACK 开启）"
                )
                return ResolvedCookie(
                    cookies_str=cookie,
                    source="admin_fallback",
                    cookies_path="datas/users/admin/cookies.json",
                )

        # 5. 环境变量兜底（仅允许在无任何 RedMuse 用户绑定的单机/遗留部署中使用）
        # 多用户系统下，u_* 用户若走到这里说明未绑定 XHS 账号，不能共用 .env cookie
        if owner and _looks_like_redmuse_user_id(owner):
            logger.warning(
                "[xhs_auth] ❌ 用户 %s 未绑定 XHS 账号，拒绝使用全局 .env COOKIES 兜底，"
                "请到「设置 → 数据源授权」绑定小红书账号。",
                owner,
            )
            return ResolvedCookie(cookies_str="", source="not_found")

        for key in ("COOKIES", "COOKIE"):
            v = (os.getenv(key) or "").strip()
            if v and "xxx" not in v.lower():
                logger.warning(
                    "[xhs_auth] ⚠️ 使用全局 .env %s 兜底（owner=%s），"
                    "多用户场景下会导致 XHS 账号共享，建议每位用户单独绑定账号。",
                    key,
                    owner or "unknown",
                )
                return ResolvedCookie(cookies_str=v, source="env_compat")

        return ResolvedCookie(cookies_str="", source="not_found")

    # ----- 内部辅助 -----

    def _try_live_cookie(self, owner_user_id: Optional[str]) -> Optional[ResolvedCookie]:
        """尝试从活浏览器取最新 cookie（同步包装，内部用 asyncio.run_coroutine_threadsafe）。

        仅当 LIVE_COOKIE_ENABLED=true 且能找到 username/cookies_path 时生效。
        任何失败均静默返回 None，调用方继续走后续解析步骤。
        """
        import os as _os
        if _os.environ.get("LIVE_COOKIE_ENABLED", "false").lower() not in ("1", "true", "yes"):
            return None

        owner = (owner_user_id or "").strip()
        if not owner:
            return None

        try:
            # 找到 username 和 cookies_path
            username, cookies_path_str = self._resolve_username_and_cookies_path(owner)
            if not username or not cookies_path_str:
                return None

            cookies_path = _resolve_path(cookies_path_str)

            # 异步调用：在当前事件循环中 await，或新建事件循环
            import asyncio as _asyncio
            from viral_agent.services.auth.live_cookie_provider import LiveCookieProvider

            try:
                loop = _asyncio.get_running_loop()
                # 已有事件循环（async 上下文）：用 run_coroutine_threadsafe 在新线程跑
                import concurrent.futures
                future = _asyncio.run_coroutine_threadsafe(
                    _live_get(username, cookies_path),
                    loop,
                )
                cookie_str = future.result(timeout=35)
            except RuntimeError:
                # 无事件循环（同步上下文）：直接 asyncio.run
                cookie_str = _asyncio.run(_live_get(username, cookies_path))

            if cookie_str:
                logger.info(
                    "[xhs_auth] live_browser cookie 命中 username={} cookie_len={}",
                    username, len(cookie_str),
                )
                return ResolvedCookie(
                    cookies_str=cookie_str,
                    source="live_browser",
                    cookies_path=cookies_path_str,
                    redmuse_user_id=owner if owner.startswith("u_") else None,
                )
        except Exception as exc:
            logger.warning("[xhs_auth] LiveCookie 失败，跌落静态 cookie: {}", exc)

        return None

    def _resolve_username_and_cookies_path(self, owner: str) -> tuple[str, str]:
        """从 owner_user_id 找到 (browser_data_dirname, cookies_path)，找不到返回 ('', '')。

        browser_data_dirname 是 LiveCookieProvider 用来拼 browser_data/<name> 的目录名。
        QR 登录建目录时规则是 xhs_{redmuse_username}[_{suffix}]，因此：
          1. 先通过 RedMuse user_store 查出 redmuse_username（如 "admin"）
          2. 扫 browser_data/ 找第一个以 xhs_{username} 开头的目录
          3. 兜底：从 cookies_path 里提取目录段（旧行为，可能得到 xhs_272529824448）
        """
        from pathlib import Path as _Path

        _browser_data_base = REPO_ROOT / "browser_data"

        if _looks_like_redmuse_user_id(owner):
            credential = self.store.get_by_redmuse_user_id(owner)
            if credential and credential.cookies_path:
                # 步骤 1：通过 RedMuse user_store 查真实登录 username（如 "admin"）
                redmuse_username = self._resolve_redmuse_username(owner)
                if redmuse_username:
                    # 步骤 2：扫 browser_data/ 找 xhs_{redmuse_username} 开头的目录
                    prefix = f"xhs_{redmuse_username}"
                    if _browser_data_base.exists():
                        for d in sorted(_browser_data_base.iterdir()):
                            if d.is_dir() and d.name.lower().startswith(prefix.lower()):
                                return d.name, credential.cookies_path
                    # 未找到目录但 username 确定，返回无 suffix 形式让 LiveCookieProvider 报明确错误
                    return prefix, credential.cookies_path

                # 步骤 3：兜底，从 cookies_path 反推目录段
                import re
                m = re.search(r"datas/users/([^/]+)/cookies\.json", credential.cookies_path)
                username = m.group(1) if m else ""
                return username, credential.cookies_path

        # 兼容旧路径：XHS 数字 ID
        if owner and not _looks_like_redmuse_user_id(owner):
            cookie_str, path = self._lookup_legacy_username_cookie(owner)
            if path:
                import re
                m = re.search(r"datas/users/([^/]+)/cookies\.json", path)
                username = m.group(1) if m else ""
                return username, path

        return "", ""

    @staticmethod
    def _resolve_redmuse_username(redmuse_user_id: str) -> str:
        """通过 RedMuse user_store 查用户名（如 'admin'）；失败返回空串。"""
        try:
            from ..redmuse_auth.user_store import get_user_store
            user = get_user_store().get_by_user_id(redmuse_user_id)
            if user and user.username:
                return user.username.strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _lookup_legacy_username_cookie(xhs_user_id: str) -> tuple[str, Optional[str]]:
        try:
            from ..identity_store import get_identity_store

            record = get_identity_store().get(xhs_user_id)
        except Exception:
            return "", None
        if not record:
            return "", None
        username = str(record.get("username") or "").strip()
        if not username:
            return "", None
        path = REPO_ROOT / "datas" / "users" / username / "cookies.json"
        cookie = _read_cookie_file(path)
        rel = f"datas/users/{username}/cookies.json"
        return cookie or "", rel if cookie else None


def _admin_fallback_enabled() -> bool:
    return (os.getenv("ALLOW_ADMIN_COOKIE_FALLBACK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


async def _live_get(username: str, cookies_path: Path) -> Optional[str]:
    """协程：获取 LiveCookieProvider 并取 fresh cookie。"""
    from viral_agent.services.auth.live_cookie_provider import LiveCookieProvider
    provider = await LiveCookieProvider.get_for_identity(username, cookies_path)
    return await provider.get_fresh_cookies()


_default_resolver: Optional[XhsCredentialResolver] = None
_default_resolver_lock = threading.Lock()


def get_credential_resolver() -> XhsCredentialResolver:
    global _default_resolver
    if _default_resolver is None:
        with _default_resolver_lock:
            if _default_resolver is None:
                _default_resolver = XhsCredentialResolver()
    return _default_resolver
