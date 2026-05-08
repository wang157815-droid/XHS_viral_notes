"""把"RedMuse 用户 user_id"翻译成"可发起 XHS 请求的 Cookie 字符串"。

调用方（CrawlerAgent / cookie_health_service / 其它需要 Cookie 的代码）唯一
入口，避免重复实现回退逻辑。

解析顺序（Phase 1）：

1. ``XHS_COOKIES_OVERRIDE`` 环境变量（调试 / 应急）
2. :class:`XhsCredentialStore` 中以 ``redmuse_user_id`` 命中的记录
   → 读取 ``cookies_path`` 指向的 ``cookies.json``
3. 兼容老路径（仅当 owner 看上去像 XHS user_id 而不是 RedMuse user_id 时启用）
   → ``identity_store`` 查 username → ``datas/users/<username>/cookies.json``
4. ``ALLOW_ADMIN_COOKIE_FALLBACK=true`` 时回退 ``datas/users/admin/cookies.json``
5. ``COOKIES`` / ``COOKIE`` 环境变量（兼容旧 viral_app .env）

注意：
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
    source: str  # "credential" | "legacy_username" | "admin_fallback" | "env_override" | "env_compat"
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

        # 5. 环境变量兜底（兼容旧 viral_app）
        for key in ("COOKIES", "COOKIE"):
            v = (os.getenv(key) or "").strip()
            if v and "xxx" not in v.lower():
                return ResolvedCookie(cookies_str=v, source="env_compat")

        return ResolvedCookie(cookies_str="", source="not_found")

    # ----- 内部辅助 -----
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


_default_resolver: Optional[XhsCredentialResolver] = None
_default_resolver_lock = threading.Lock()


def get_credential_resolver() -> XhsCredentialResolver:
    global _default_resolver
    if _default_resolver is None:
        with _default_resolver_lock:
            if _default_resolver is None:
                _default_resolver = XhsCredentialResolver()
    return _default_resolver
