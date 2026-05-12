"""RedMuse 系统用户存储（JSON 文件版）。

Phase 0 用 JSON 文件，Phase 5 替换为 PostgreSQL 表。

并发安全：单进程内通过 :class:`threading.RLock` 串行化读写。多进程部署时 JSON 文件
仍是单一真相来源，但需要更强的并发原语；Phase 5 会随存储迁移一并解决。

字段约定：

.. code-block:: text

    {
      "version": 1,
      "users": [
        {
          "user_id": "u_xxxxxxxx",          # 由 store 生成
          "username": "admin",                # 唯一索引（小写比较）
          "nickname": "管理员",
          "password_hash": "$2b$12$...",
          "role": "admin" | "user",
          "status": "active" | "disabled",
          "xhs_credential_path": "datas/users/admin/cookies.json",
          "created_at": ISO 时间,
          "updated_at": ISO 时间,
          "last_login_at": ISO 时间 | null
        }
      ]
    }
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.security import normalize_role
from .password_hash import hash_password, verify_password


class UserNotFoundError(LookupError):
    """指定用户名/ID 不存在。"""


class UserAlreadyExistsError(ValueError):
    """同名用户已存在。"""


_DEFAULT_STORE_FILE = "datas/redmuse_auth/users.json"
_STORE_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RedMuseUser:
    user_id: str
    username: str
    nickname: str
    password_hash: str
    role: str = "analyst"
    status: str = "active"
    xhs_credential_path: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_login_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> Dict[str, Any]:
        """对外暴露的安全视图，剔除 password_hash。"""
        data = self.to_dict()
        data.pop("password_hash", None)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RedMuseUser":
        return cls(
            user_id=str(data["user_id"]),
            username=str(data["username"]),
            nickname=str(data.get("nickname") or data["username"]),
            password_hash=str(data["password_hash"]),
            role=normalize_role(data.get("role") or "analyst"),
            status=str(data.get("status") or "active"),
            xhs_credential_path=(
                str(data["xhs_credential_path"])
                if data.get("xhs_credential_path")
                else None
            ),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            last_login_at=(
                str(data["last_login_at"]) if data.get("last_login_at") else None
            ),
        )


class RedMuseUserStore:
    """JSON 文件实现的 RedMuse 用户表。"""

    def __init__(self, store_file: str = _DEFAULT_STORE_FILE) -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ----- 内部 IO -----
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": _STORE_VERSION, "users": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": _STORE_VERSION, "users": []}
        if not isinstance(data, dict):
            return {"version": _STORE_VERSION, "users": []}
        if not isinstance(data.get("users"), list):
            data["users"] = []
        data.setdefault("version", _STORE_VERSION)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        # 原子写：先写 .tmp 再 rename，避免崩溃时半截文件
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _new_user_id() -> str:
        return f"u_{secrets.token_hex(8)}"

    @staticmethod
    def _normalize_username(username: str) -> str:
        return (username or "").strip().lower()

    # ----- 查询 -----
    def list_users(self) -> List[RedMuseUser]:
        with self._lock:
            data = self._load()
            return [RedMuseUser.from_dict(item) for item in data.get("users", [])]

    def get_by_user_id(self, user_id: str) -> Optional[RedMuseUser]:
        target = (user_id or "").strip()
        if not target:
            return None
        with self._lock:
            for item in self._load().get("users", []):
                if str(item.get("user_id")) == target:
                    return RedMuseUser.from_dict(item)
        return None

    def get_by_username(self, username: str) -> Optional[RedMuseUser]:
        target = self._normalize_username(username)
        if not target:
            return None
        with self._lock:
            for item in self._load().get("users", []):
                if self._normalize_username(item.get("username", "")) == target:
                    return RedMuseUser.from_dict(item)
        return None

    def count(self) -> int:
        with self._lock:
            return len(self._load().get("users", []))

    # ----- 创建 / 更新 -----
    @staticmethod
    def is_valid_role(role: str) -> bool:
        return role in ("admin", "user", "analyst", "viewer")

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

        with self._lock:
            data = self._load()
            users: List[Dict[str, Any]] = list(data.get("users") or [])
            for item in users:
                if self._normalize_username(item.get("username", "")) == normalized:
                    raise UserAlreadyExistsError(f"用户名已存在: {username}")

            user = RedMuseUser(
                user_id=self._new_user_id(),
                username=normalized,
                nickname=(nickname or username).strip(),
                password_hash=hash_password(password),
                role=normalized_role,
                status=status,
                xhs_credential_path=xhs_credential_path,
            )
            users.append(user.to_dict())
            data["users"] = users
            self._save(data)
            return user

    def set_password(self, user_id: str, new_password: str) -> RedMuseUser:
        with self._lock:
            data = self._load()
            users: List[Dict[str, Any]] = list(data.get("users") or [])
            for index, item in enumerate(users):
                if str(item.get("user_id")) == user_id:
                    item["password_hash"] = hash_password(new_password)
                    item["updated_at"] = _now_iso()
                    users[index] = item
                    data["users"] = users
                    self._save(data)
                    return RedMuseUser.from_dict(item)
        raise UserNotFoundError(user_id)

    def set_role(self, user_id: str, role: str) -> RedMuseUser:
        if not self.is_valid_role(role):
            raise ValueError(f"非法角色: {role}")
        return self._patch(user_id, {"role": normalize_role(role)})

    def set_status(self, user_id: str, status: str) -> RedMuseUser:
        if status not in ("active", "disabled"):
            raise ValueError(f"非法状态: {status}")
        return self._patch(user_id, {"status": status})

    def set_xhs_credential_path(
        self, user_id: str, path: Optional[str]
    ) -> RedMuseUser:
        return self._patch(user_id, {"xhs_credential_path": path})

    def touch_last_login(self, user_id: str) -> Optional[RedMuseUser]:
        try:
            return self._patch(user_id, {"last_login_at": _now_iso()})
        except UserNotFoundError:
            return None

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            data = self._load()
            users: List[Dict[str, Any]] = list(data.get("users") or [])
            new_users = [item for item in users if str(item.get("user_id")) != user_id]
            if len(new_users) == len(users):
                return False
            data["users"] = new_users
            self._save(data)
            return True

    # ----- 校验 -----
    def authenticate(
        self, username: str, password: str
    ) -> Optional[RedMuseUser]:
        user = self.get_by_username(username)
        if not user:
            return None
        if user.status != "active":
            return None
        if not verify_password(password, user.password_hash):
            return None
        self.touch_last_login(user.user_id)
        # 重新加载以拿到 last_login_at 更新后的副本
        return self.get_by_user_id(user.user_id) or user

    # ----- 内部辅助 -----
    def _patch(self, user_id: str, patch: Dict[str, Any]) -> RedMuseUser:
        with self._lock:
            data = self._load()
            users: List[Dict[str, Any]] = list(data.get("users") or [])
            for index, item in enumerate(users):
                if str(item.get("user_id")) == user_id:
                    item.update(patch)
                    item["updated_at"] = _now_iso()
                    users[index] = item
                    data["users"] = users
                    self._save(data)
                    return RedMuseUser.from_dict(item)
        raise UserNotFoundError(user_id)


_default_store: Optional[RedMuseUserStore] = None
_default_store_lock = threading.Lock()


def get_user_store():
    """单例访问器，测试可通过 monkeypatch 替换 ``_default_store``。"""
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                backend = (os.getenv("REDMUSE_USER_STORE_BACKEND") or "json").strip().lower()
                if backend == "pg":
                    from ...infrastructure.repository.pg_user_store import PgRedMuseUserStore

                    _default_store = PgRedMuseUserStore()
                else:
                    _default_store = RedMuseUserStore()
    return _default_store
