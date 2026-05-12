import os
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
security = HTTPBearer(auto_error=False)


class RoleLevel(IntEnum):
    viewer = 1
    analyst = 2
    admin = 3


def normalize_role(raw: Any) -> str:
    role = str(raw or "viewer").strip().lower()
    if role == "user":
        return "analyst"
    if role in ("viewer", "analyst", "admin"):
        return role
    return "viewer"


def role_level(raw: Any) -> RoleLevel:
    return RoleLevel[normalize_role(raw)]


def role_allows(raw: Any, minimum: RoleLevel) -> bool:
    return role_level(raw) >= minimum


def _get_jwt_module():
    try:
        import jwt  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少依赖 PyJWT，请先执行: pip install PyJWT") from exc
    return jwt


def _resolve_jwt_secret() -> str:
    explicit_secret = os.getenv("REDMUSE_JWT_SECRET") or os.getenv("JWT_SECRET")
    is_production = os.getenv("PRODUCTION", "false").lower() == "true"

    if explicit_secret:
        return explicit_secret

    if is_production:
        raise RuntimeError("生产环境必须配置 REDMUSE_JWT_SECRET 或 JWT_SECRET")

    # 开发环境兜底，避免阶段切片联调被阻断
    logger.warning("未配置 REDMUSE_JWT_SECRET，当前使用开发环境临时密钥")
    return "redmuse-dev-only-secret-change-me"


def create_access_token(
    user: Dict[str, Any],
    expires_hours: int = JWT_EXPIRE_HOURS,
    *,
    token_type: str = "redmuse",
) -> str:
    """签发 RedMuse 系统 JWT。

    Args:
        user: 必须包含 ``user_id``，可选 ``nickname / role / username``。
        token_type: ``"redmuse"`` 表示来自 Phase 0+ 用户名/密码登录；
            ``"xhs_selfinfo"`` 表示旧版 XHS 扫码登录（Phase 2 后会移除）。
            前端无需关心；后端 ``get_current_user`` 会原样回传，便于 Phase 1+ 做来源审计。
    """
    jwt = _get_jwt_module()
    secret = _resolve_jwt_secret()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "nickname": user.get("nickname", ""),
        "role": normalize_role(user.get("role", "analyst")),
        "username": user.get("username", ""),
        "token_type": token_type,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    jwt = _get_jwt_module()
    secret = _resolve_jwt_secret()
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录认证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="无效的 Token")

        return {
            "user_id": user_id,
            "nickname": payload.get("nickname", ""),
            "role": normalize_role(payload.get("role", "analyst")),
            "username": payload.get("username", ""),
            "token_type": payload.get("token_type", "redmuse"),
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        jwt = _get_jwt_module()
        if isinstance(exc, jwt.ExpiredSignatureError):
            raise HTTPException(status_code=401, detail="Token 已过期，请重新登录") from exc
        if isinstance(exc, jwt.InvalidTokenError):
            raise HTTPException(status_code=401, detail="无效的 Token") from exc
        raise


def require_role(minimum: RoleLevel):
    def dependency(
        current_user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        if not role_allows(current_user.get("role"), minimum):
            raise HTTPException(status_code=403, detail="权限不足")
        return current_user

    return dependency


def require_admin_user(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """FastAPI Dependency：仅允许管理员访问。非管理员直接 403。"""
    return require_role(RoleLevel.admin)(current_user)

