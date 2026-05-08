from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger


class CookieHealthService:
    """
    Cookie 健康状态服务（阶段1）

    状态定义：
    - valid: 当前 Cookie 可用
    - expiring_soon: Cookie 可用但保存天数 >= 5 天
    - expired: Cookie 无效或不存在
    - unknown: 暂时无法判定（依赖未就绪/检查异常）
    """

    EXPIRING_SOON_DAYS = 5
    CACHE_TTL_SECONDS = 300

    def __init__(self, cache_file: str = "datas/auth/cookie_health_cache.json") -> None:
        self.repo_root = Path(__file__).resolve().parents[3]
        cache_path = Path(cache_file)
        self.cache_path = cache_path if cache_path.is_absolute() else (self.repo_root / cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def get_cookie_health(
        self,
        current_user: Optional[dict] = None,
        force_check: bool = False,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        username = self._resolve_username(current_user)
        cookie_info = self._get_cookie_info(username)

        if not cookie_info or not cookie_info.get("has_cookie"):
            return {
                "status": "expired",
                "saved_days": 0,
                "last_checked_at": now.isoformat(),
                "message": "未检测到可用 Cookie，请重新登录",
            }

        # 优先用 created_at（首次保存时间）计算天数，fallback updated_at
        saved_since = self._parse_iso(
            cookie_info.get("created_at") or cookie_info.get("updated_at")
        )
        saved_days = 0
        if saved_since:
            delta_seconds = (now - saved_since).total_seconds()
            saved_days = max(0, int(delta_seconds // 86400))

        cache = self._load_cache()
        cache_record = cache.get(username, {})
        cache_checked_at = self._parse_iso(cache_record.get("last_checked_at"))
        cookie_updated_at = cookie_info.get("updated_at")

        needs_check = (
            force_check
            or not cache_record
            or not cache_checked_at
            or (now - cache_checked_at).total_seconds() >= self.CACHE_TTL_SECONDS
            or cache_record.get("cookie_updated_at") != cookie_updated_at
        )

        if needs_check:
            status, message = self._check_cookie_validity(username)
            cache_record = {
                "status": status,
                "message": message,
                "last_checked_at": now.isoformat(),
                "cookie_updated_at": cookie_updated_at,
            }
            cache[username] = cache_record
            self._save_cache(cache)

        status = cache_record.get("status", "unknown")
        message = cache_record.get("message", "Cookie 状态检测中...")
        last_checked_at = cache_record.get("last_checked_at", now.isoformat())

        if status == "valid" and saved_days >= self.EXPIRING_SOON_DAYS:
            status = "expiring_soon"
            message = f"Cookie 已保存 {saved_days} 天，建议尽快重新登录刷新"

        return {
            "status": status,
            "saved_days": saved_days,
            "last_checked_at": last_checked_at,
            "message": message,
        }

    def _check_cookie_validity(self, username: str) -> Tuple[str, str]:
        try:
            from apis.xhs_pc_apis import XHS_Apis

            cookie = self._get_cookie_value(username)
            if not cookie:
                return "expired", "未检测到 Cookie，请重新登录"

            xhs_api = XHS_Apis()
            success, msg, _ = xhs_api.get_user_self_info(cookie)
            if success:
                return "valid", "Cookie 有效"

            reason = msg or "账号未登录"
            return "expired", f"Cookie 校验失败：{reason}"
        except Exception as exc:
            logger.warning(f"Cookie 健康检查异常（{username}）: {exc}")
            return "unknown", f"Cookie 检测异常：{str(exc)}"

    @staticmethod
    def _resolve_username(current_user: Optional[dict]) -> str:
        """解析 cookie 文件所在的目录名（``datas/users/<dirname>/cookies.json``）。

        优先级（Phase 1）：

        1. JWT 中以 ``u_`` 开头的 RedMuse ``user_id`` →
           :class:`XhsCredentialStore` 查 ``cookies_path`` → 抽取目录名
        2. 旧 XHS user_id → ``IdentityStore`` 查 username
        3. JWT 中明文 ``username``（兼容旧 token；RedMuse 的 username 也允许，
           只要项目里碰巧有同名目录即可——通常用于 admin）
        4. 最后回退 ``admin``（开发场景兜底；生产应通过 XhsCredentialStore 绑定）
        """
        cu = current_user or {}
        user_id = str(cu.get("user_id") or "").strip()

        # 1. RedMuse 用户：通过 XhsCredentialStore 找 cookies_path
        if user_id.startswith("u_"):
            try:
                from .xhs_auth import get_credential_store

                cred = get_credential_store().get_by_redmuse_user_id(user_id)
                if cred and cred.cookies_path:
                    parts = Path(cred.cookies_path).parts
                    if "users" in parts:
                        idx = parts.index("users")
                        if idx + 1 < len(parts):
                            return parts[idx + 1]
            except Exception as exc:
                logger.debug(
                    f"Cookie 健康检查按 RedMuse user_id 解析失败 ({user_id}): {exc}"
                )

        # 2. 兼容旧 XHS user_id（identity_store）
        if user_id and not user_id.startswith("u_"):
            try:
                from .identity_store import get_identity_store

                record = get_identity_store().get(user_id)
                if record:
                    name = str(record.get("username") or "").strip()
                    if name:
                        return name
            except Exception as exc:
                logger.debug(
                    f"Cookie 健康检查按 XHS user_id 解析用户名失败 ({user_id}): {exc}"
                )

        # 3. JWT 明文 username（旧 token 或开发环境）
        direct = str(cu.get("username") or "").strip()
        if direct:
            return direct

        # 4. 兜底
        return "admin"

    @staticmethod
    def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            text = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def _get_cookie_info(self, username: str) -> Optional[dict]:
        for path in self._candidate_cookie_files(username):
            info = self._read_cookie_file(path)
            if info and info.get("has_cookie"):
                return info
        return None

    def _get_cookie_value(self, username: str) -> Optional[str]:
        info = self._get_cookie_info(username)
        if not info:
            return None
        cookie = info.get("cookie")
        if isinstance(cookie, str) and cookie.strip():
            return cookie
        return None

    def _candidate_cookie_files(self, username: str) -> list[Path]:
        files: list[Path] = []
        names = [username]
        # 多用户模式下不能回退到 admin，否则不同设备/账号会互相使用 Cookie。
        # 如需兼容旧部署，可显式开启 ALLOW_ADMIN_COOKIE_FALLBACK。
        if username != "admin" and os.getenv("ALLOW_ADMIN_COOKIE_FALLBACK", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            names.append("admin")

        for name in names:
            files.append(self.repo_root / "datas" / "users" / name / "cookies.json")
        return files

    @staticmethod
    def _read_cookie_file(path: Path) -> Optional[dict]:
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Cookie 文件解析失败: {path} ({exc})")
            return None

        cookie = payload.get("cookie")
        has_cookie = isinstance(cookie, str) and bool(cookie.strip())
        return {
            "has_cookie": has_cookie,
            "cookie": cookie if has_cookie else None,
            "cookie_length": len(cookie) if has_cookie else 0,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "source": str(path),
        }

    def _load_cache(self) -> Dict[str, dict]:
        if not self.cache_path.exists():
            return {}
        try:
            content = self.cache_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def _save_cache(self, data: Dict[str, dict]) -> None:
        self.cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


cookie_health_service = CookieHealthService()

