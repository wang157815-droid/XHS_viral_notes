from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ...core.security import normalize_role
from ...services.redmuse_auth.password_hash import hash_password, verify_password
from ...services.redmuse_auth.user_store import (
    RedMuseUser,
    UserAlreadyExistsError,
    UserNotFoundError,
    _now_iso,
)
from ..db.engine import BusinessDbUnavailable, get_business_db_session


_USER_COLUMNS = """
user_id,
username,
nickname,
password_hash,
role,
status,
xhs_credential_path,
created_at,
updated_at,
last_login_at
"""


class PgRedMuseUserStore:
    def __init__(self) -> None:
        self.path = None

    @staticmethod
    def _new_user_id() -> str:
        return f"u_{secrets.token_hex(8)}"

    @staticmethod
    def _normalize_username(username: str) -> str:
        return (username or "").strip().lower()

    @staticmethod
    def is_valid_role(role: str) -> bool:
        return role in ("admin", "user", "analyst", "viewer")

    def list_users(self) -> list[RedMuseUser]:
        with get_business_db_session() as session:
            rows = session.execute(
                text(f"SELECT {_USER_COLUMNS} FROM redmuse_users ORDER BY created_at ASC")
            ).mappings().all()
            return [self._row_to_user(dict(row)) for row in rows]

    def get_by_user_id(self, user_id: str) -> Optional[RedMuseUser]:
        target = (user_id or "").strip()
        if not target:
            return None
        with get_business_db_session() as session:
            row = session.execute(
                text(f"SELECT {_USER_COLUMNS} FROM redmuse_users WHERE user_id = :user_id"),
                {"user_id": target},
            ).mappings().first()
            return self._row_to_user(dict(row)) if row else None

    def get_by_username(self, username: str) -> Optional[RedMuseUser]:
        target = self._normalize_username(username)
        if not target:
            return None
        with get_business_db_session() as session:
            row = session.execute(
                text(f"SELECT {_USER_COLUMNS} FROM redmuse_users WHERE username = :username"),
                {"username": target},
            ).mappings().first()
            return self._row_to_user(dict(row)) if row else None

    def count(self) -> int:
        with get_business_db_session() as session:
            value = session.execute(text("SELECT COUNT(*) FROM redmuse_users")).scalar_one()
            return int(value)

    def create_user(
        self,
        *,
        username: str,
        password: str,
        nickname: Optional[str] = None,
        role: str = "analyst",
        status: str = "active",
        xhs_credential_path: Optional[str] = None,
    ) -> RedMuseUser:
        normalized = self._normalize_username(username)
        if not normalized:
            raise ValueError("用户名不能为空")
        if not self.is_valid_role(role):
            raise ValueError(f"非法角色: {role}")
        normalized_role = normalize_role(role)
        if status not in ("active", "disabled"):
            raise ValueError(f"非法状态: {status}")

        payload = {
            "user_id": self._new_user_id(),
            "username": normalized,
            "nickname": (nickname or username).strip(),
            "password_hash": hash_password(password),
            "role": normalized_role,
            "status": status,
            "xhs_credential_path": xhs_credential_path,
        }
        try:
            with get_business_db_session() as session:
                row = session.execute(
                    text(
                        f"""
                        INSERT INTO redmuse_users (
                            user_id,
                            username,
                            nickname,
                            password_hash,
                            role,
                            status,
                            xhs_credential_path
                        ) VALUES (
                            :user_id,
                            :username,
                            :nickname,
                            :password_hash,
                            :role,
                            :status,
                            :xhs_credential_path
                        ) RETURNING {_USER_COLUMNS}
                        """
                    ),
                    payload,
                ).mappings().one()
                return self._row_to_user(dict(row))
        except IntegrityError as exc:
            raise UserAlreadyExistsError(f"用户名已存在: {username}") from exc

    def set_password(self, user_id: str, new_password: str) -> RedMuseUser:
        return self._patch(user_id, {"password_hash": hash_password(new_password)})

    def set_role(self, user_id: str, role: str) -> RedMuseUser:
        if not self.is_valid_role(role):
            raise ValueError(f"非法角色: {role}")
        return self._patch(user_id, {"role": normalize_role(role)})

    def set_status(self, user_id: str, status: str) -> RedMuseUser:
        if status not in ("active", "disabled"):
            raise ValueError(f"非法状态: {status}")
        return self._patch(user_id, {"status": status})

    def set_xhs_credential_path(self, user_id: str, path: Optional[str]) -> RedMuseUser:
        return self._patch(user_id, {"xhs_credential_path": path})

    def touch_last_login(self, user_id: str) -> Optional[RedMuseUser]:
        try:
            return self._patch(user_id, {"last_login_at": _now_iso()})
        except UserNotFoundError:
            return None

    def delete_user(self, user_id: str) -> bool:
        with get_business_db_session() as session:
            result = session.execute(
                text("DELETE FROM redmuse_users WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            return bool(result.rowcount)

    def authenticate(self, username: str, password: str) -> Optional[RedMuseUser]:
        user = self.get_by_username(username)
        if not user:
            return None
        if user.status != "active":
            return None
        if not verify_password(password, user.password_hash):
            return None
        self.touch_last_login(user.user_id)
        return self.get_by_user_id(user.user_id) or user

    def _patch(self, user_id: str, patch: Dict[str, Any]) -> RedMuseUser:
        if not patch:
            current = self.get_by_user_id(user_id)
            if not current:
                raise UserNotFoundError(user_id)
            return current
        assignments = ", ".join(f"{key} = :{key}" for key in patch)
        params = dict(patch)
        params["user_id"] = user_id
        try:
            with get_business_db_session() as session:
                row = session.execute(
                    text(
                        f"""
                        UPDATE redmuse_users
                        SET {assignments}, updated_at = NOW()
                        WHERE user_id = :user_id
                        RETURNING {_USER_COLUMNS}
                        """
                    ),
                    params,
                ).mappings().first()
                if not row:
                    raise UserNotFoundError(user_id)
                return self._row_to_user(dict(row))
        except SQLAlchemyError as exc:
            if isinstance(exc, UserNotFoundError):
                raise
            raise BusinessDbUnavailable(str(exc)) from exc

    @staticmethod
    def _row_to_user(row: Dict[str, Any]) -> RedMuseUser:
        return RedMuseUser.from_dict(
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "nickname": row.get("nickname") or row["username"],
                "password_hash": row["password_hash"],
                "role": normalize_role(row.get("role") or "analyst"),
                "status": row.get("status") or "active",
                "xhs_credential_path": row.get("xhs_credential_path"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "last_login_at": row.get("last_login_at"),
            }
        )
