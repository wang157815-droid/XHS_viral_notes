"""XHS 数据源凭据存储（JSON 文件版，Phase 1）。

字段约定（``datas/redmuse_auth/xhs_credentials.json``）::

    {
      "version": 1,
      "credentials": [
        {
          "redmuse_user_id": "u_xxxxxxxx",
          "xhs_user_id": "5e8c7b...",                # 来自 selfinfo（可空：未授权）
          "xhs_nickname": "...",                       # 用于 UI 展示
          "cookies_path": "datas/users/admin/cookies.json",
          "status": "active|expired|expiring_soon|unknown|unbound",
          "status_message": "...",
          "last_validated_at": ISO 时间 | null,
          "created_at": ISO 时间,
          "updated_at": ISO 时间
        }
      ]
    }

并发：单进程内 RLock，多进程留给 Phase 5 数据库迁移。
原子写：先 write tmp → replace。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_STORE_FILE = "datas/redmuse_auth/xhs_credentials.json"
_STORE_VERSION = 1

VALID_STATUSES = ("active", "expired", "expiring_soon", "unknown", "unbound")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class XhsCredential:
    """一条「RedMuse 用户 → XHS Cookie」绑定记录。"""

    redmuse_user_id: str
    cookies_path: str
    xhs_user_id: Optional[str] = None
    xhs_nickname: Optional[str] = None
    status: str = "unknown"
    status_message: str = ""
    last_validated_at: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> Dict[str, Any]:
        """对外（含前端）暴露的字段：不暴露 cookies_path 绝对路径细节。"""
        return {
            "redmuse_user_id": self.redmuse_user_id,
            "xhs_user_id": self.xhs_user_id,
            "xhs_nickname": self.xhs_nickname,
            "status": self.status,
            "status_message": self.status_message,
            "last_validated_at": self.last_validated_at,
            "is_bound": bool(self.cookies_path) and self.status != "unbound",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XhsCredential":
        return cls(
            redmuse_user_id=str(data["redmuse_user_id"]),
            cookies_path=str(data.get("cookies_path") or ""),
            xhs_user_id=(
                str(data["xhs_user_id"]) if data.get("xhs_user_id") else None
            ),
            xhs_nickname=(
                str(data["xhs_nickname"]) if data.get("xhs_nickname") else None
            ),
            status=str(data.get("status") or "unknown"),
            status_message=str(data.get("status_message") or ""),
            last_validated_at=(
                str(data["last_validated_at"])
                if data.get("last_validated_at")
                else None
            ),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
        )


class XhsCredentialStore:
    """JSON 文件实现的 XHS 凭据表。"""

    def __init__(self, store_file: str = _DEFAULT_STORE_FILE) -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ----- 内部 IO -----
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": _STORE_VERSION, "credentials": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": _STORE_VERSION, "credentials": []}
        if not isinstance(data, dict):
            return {"version": _STORE_VERSION, "credentials": []}
        if not isinstance(data.get("credentials"), list):
            data["credentials"] = []
        data.setdefault("version", _STORE_VERSION)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----- 查询 -----
    def list_credentials(self) -> List[XhsCredential]:
        with self._lock:
            data = self._load()
            return [XhsCredential.from_dict(item) for item in data.get("credentials", [])]

    def get_by_redmuse_user_id(self, redmuse_user_id: str) -> Optional[XhsCredential]:
        target = (redmuse_user_id or "").strip()
        if not target:
            return None
        with self._lock:
            for item in self._load().get("credentials", []):
                if str(item.get("redmuse_user_id")) == target:
                    return XhsCredential.from_dict(item)
        return None

    def get_by_xhs_user_id(self, xhs_user_id: str) -> Optional[XhsCredential]:
        target = (xhs_user_id or "").strip()
        if not target:
            return None
        with self._lock:
            for item in self._load().get("credentials", []):
                if str(item.get("xhs_user_id") or "") == target:
                    return XhsCredential.from_dict(item)
        return None

    def count(self) -> int:
        with self._lock:
            return len(self._load().get("credentials", []))

    # ----- 写入 -----
    def upsert(
        self,
        *,
        redmuse_user_id: str,
        cookies_path: str,
        xhs_user_id: Optional[str] = None,
        xhs_nickname: Optional[str] = None,
        status: Optional[str] = None,
        status_message: Optional[str] = None,
        last_validated_at: Optional[str] = None,
    ) -> XhsCredential:
        """新建或更新一条 credential。``cookies_path`` 为必填业务字段。"""
        if not redmuse_user_id:
            raise ValueError("redmuse_user_id 不能为空")
        if not cookies_path:
            raise ValueError("cookies_path 不能为空")
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"非法状态: {status}")

        with self._lock:
            data = self._load()
            credentials: List[Dict[str, Any]] = list(data.get("credentials") or [])
            now = _now_iso()
            existing_idx: Optional[int] = None
            for idx, item in enumerate(credentials):
                if str(item.get("redmuse_user_id")) == redmuse_user_id:
                    existing_idx = idx
                    break

            if existing_idx is None:
                record = XhsCredential(
                    redmuse_user_id=redmuse_user_id,
                    cookies_path=cookies_path,
                    xhs_user_id=xhs_user_id,
                    xhs_nickname=xhs_nickname,
                    status=status or "unknown",
                    status_message=status_message or "",
                    last_validated_at=last_validated_at,
                    created_at=now,
                    updated_at=now,
                )
                credentials.append(record.to_dict())
            else:
                merged = dict(credentials[existing_idx])
                merged["cookies_path"] = cookies_path
                if xhs_user_id is not None:
                    merged["xhs_user_id"] = xhs_user_id
                if xhs_nickname is not None:
                    merged["xhs_nickname"] = xhs_nickname
                if status is not None:
                    merged["status"] = status
                if status_message is not None:
                    merged["status_message"] = status_message
                if last_validated_at is not None:
                    merged["last_validated_at"] = last_validated_at
                merged["updated_at"] = now
                merged.setdefault("created_at", now)
                credentials[existing_idx] = merged
                record = XhsCredential.from_dict(merged)

            data["credentials"] = credentials
            self._save(data)
            return record

    def update_status(
        self,
        redmuse_user_id: str,
        *,
        status: str,
        status_message: str = "",
        last_validated_at: Optional[str] = None,
    ) -> Optional[XhsCredential]:
        if status not in VALID_STATUSES:
            raise ValueError(f"非法状态: {status}")
        with self._lock:
            data = self._load()
            credentials: List[Dict[str, Any]] = list(data.get("credentials") or [])
            for idx, item in enumerate(credentials):
                if str(item.get("redmuse_user_id")) == redmuse_user_id:
                    item["status"] = status
                    item["status_message"] = status_message
                    if last_validated_at is not None:
                        item["last_validated_at"] = last_validated_at
                    item["updated_at"] = _now_iso()
                    credentials[idx] = item
                    data["credentials"] = credentials
                    self._save(data)
                    return XhsCredential.from_dict(item)
        return None

    def delete(self, redmuse_user_id: str) -> bool:
        with self._lock:
            data = self._load()
            credentials: List[Dict[str, Any]] = list(data.get("credentials") or [])
            new_list = [
                item
                for item in credentials
                if str(item.get("redmuse_user_id")) != redmuse_user_id
            ]
            if len(new_list) == len(credentials):
                return False
            data["credentials"] = new_list
            self._save(data)
            return True


_default_store: Optional[XhsCredentialStore] = None
_default_store_lock = threading.Lock()


def get_credential_store() -> XhsCredentialStore:
    """单例访问器，conftest 通过替换 ``_default_store`` 隔离测试。"""
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = XhsCredentialStore()
    return _default_store
