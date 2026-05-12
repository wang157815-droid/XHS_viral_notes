from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..core.security import normalize_role
from ..infrastructure.db.engine import get_business_db_session

try:
    from sqlalchemy import text

    _SA_AVAILABLE = True
except ImportError:  # pragma: no cover
    text = None  # type: ignore[assignment]
    _SA_AVAILABLE = False


class IdentityStore:
    """
    阶段1身份映射存储（文件实现）

    后续迁移到 PostgreSQL 时，只需要保持同名方法契约并替换实现。
    """

    def __init__(self, store_file: str = "datas/auth/xhs_identities.json") -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: Dict[str, dict]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert_xhs_identity(self, user_id: str, nickname: str) -> dict:
        data = self._load()
        now = datetime.now(timezone.utc).isoformat()

        if user_id in data:
            record = data[user_id]
            record["nickname"] = nickname
            record["updated_at"] = now
            if record.get("source") == "identity_link_stub":
                record["source"] = "xhs_selfinfo"
            data[user_id] = record
            self._save(data)
            return record

        # 第一位接入用户默认 admin，后续 analyst
        role = "admin" if len(data) == 0 else "analyst"
        record = {
            "user_id": user_id,
            "username": f"xhs_{user_id}",
            "nickname": nickname,
            "role": role,
            "created_at": now,
            "updated_at": now,
            "source": "xhs_selfinfo",
            "linked_user_ids": [],
        }
        data[user_id] = record
        self._save(data)
        return record

    def list_identities(self) -> List[dict]:
        data = self._load()
        items = list(data.values())
        items.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
        return items

    def get(self, user_id: str) -> Optional[dict]:
        return self._load().get(user_id)

    def set_role(self, user_id: str, role: str) -> Optional[dict]:
        if role not in ("admin", "user", "analyst", "viewer"):
            return None
        normalized_role = normalize_role(role)
        data = self._load()
        record = data.get(user_id)
        if not record:
            return None
        record["role"] = normalized_role
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[user_id] = record
        self._save(data)
        return record

    def delete(self, user_id: str) -> bool:
        data = self._load()
        if user_id not in data:
            return False
        data.pop(user_id)
        self._save(data)
        return True

    def link_user_ids(self, a: str, b: str) -> None:
        """将两个小红书 user_id 记为同一终端上的连续登录，用于合并历史任务列表。"""
        a, b = str(a).strip(), str(b).strip()
        if not a or not b or a == b:
            return
        data = self._load()
        now = datetime.now(timezone.utc).isoformat()

        def touch(uid: str, other: str) -> None:
            rec = data.get(uid)
            if not rec:
                rec = {
                    "user_id": uid,
                    "username": f"xhs_{uid}",
                    "nickname": "",
                    "role": "analyst",
                    "created_at": now,
                    "updated_at": now,
                    "source": "identity_link_stub",
                    "linked_user_ids": [],
                }
            links = list(rec.get("linked_user_ids") or [])
            if other not in links:
                links.append(other)
            rec["linked_user_ids"] = links
            rec["updated_at"] = now
            data[uid] = rec

        touch(a, b)
        touch(b, a)
        self._save(data)

    def expand_visible_user_ids(self, user_id: str) -> Set[str]:
        """当前登录 id + 与其 link 过的其它账号 id（任务 owner_user_id 可能仍是旧号）。"""
        if not user_id:
            return set()
        data = self._load()
        out: Set[str] = {user_id}
        rec = data.get(user_id)
        if rec:
            for x in rec.get("linked_user_ids") or []:
                xs = str(x).strip()
                if xs:
                    out.add(xs)
        for uid, r in data.items():
            linked = [str(x).strip() for x in (r.get("linked_user_ids") or []) if str(x).strip()]
            if user_id in linked:
                out.add(str(uid))
                out.update(linked)
        return out


class SqlAlchemyIdentityStore:
    """PostgreSQL-backed identity store."""

    def upsert_xhs_identity(self, user_id: str, nickname: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(user_id)
        if existing:
            source = existing.get("source")
            if source == "identity_link_stub":
                source = "xhs_selfinfo"
            with get_business_db_session() as session:
                session.execute(
                    text(
                        """
                        UPDATE identities
                        SET nickname = :nickname, source = :source, updated_at = CAST(:now AS timestamptz)
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": user_id, "nickname": nickname, "source": source, "now": now},
                )
            return self.get(user_id) or existing

        role = "admin" if len(self.list_identities()) == 0 else "analyst"
        record = {
            "user_id": user_id,
            "username": f"xhs_{user_id}",
            "nickname": nickname,
            "role": role,
            "created_at": now,
            "updated_at": now,
            "source": "xhs_selfinfo",
            "linked_user_ids": [],
        }
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO identities (
                        user_id, username, nickname, role, source, linked_user_ids, created_at, updated_at
                    ) VALUES (
                        :user_id, :username, :nickname, :role, :source,
                        CAST(:linked_user_ids AS jsonb), CAST(:created_at AS timestamptz),
                        CAST(:updated_at AS timestamptz)
                    )
                    """
                ),
                {**record, "linked_user_ids": json.dumps(record["linked_user_ids"], ensure_ascii=False)},
            )
        return record

    def list_identities(self) -> List[dict]:
        with get_business_db_session() as session:
            rows = session.execute(
                text("SELECT * FROM identities ORDER BY updated_at DESC")
            ).mappings().all()
        return [self._from_row(row) for row in rows]

    def get(self, user_id: str) -> Optional[dict]:
        with get_business_db_session() as session:
            row = session.execute(
                text("SELECT * FROM identities WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()
        return self._from_row(row) if row else None

    def set_role(self, user_id: str, role: str) -> Optional[dict]:
        if role not in ("admin", "user", "analyst", "viewer"):
            return None
        normalized_role = normalize_role(role)
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    UPDATE identities
                    SET role = :role, updated_at = NOW()
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": user_id, "role": normalized_role},
            )
        return self.get(user_id)

    def delete(self, user_id: str) -> bool:
        existed = self.get(user_id) is not None
        if not existed:
            return False
        with get_business_db_session() as session:
            session.execute(
                text("DELETE FROM identities WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        return True

    def link_user_ids(self, a: str, b: str) -> None:
        a, b = str(a).strip(), str(b).strip()
        if not a or not b or a == b:
            return
        now = datetime.now(timezone.utc).isoformat()
        for uid, other in ((a, b), (b, a)):
            rec = self.get(uid)
            if not rec:
                rec = {
                    "user_id": uid,
                    "username": f"xhs_{uid}",
                    "nickname": "",
                    "role": "analyst",
                    "created_at": now,
                    "updated_at": now,
                    "source": "identity_link_stub",
                    "linked_user_ids": [],
                }
                self._insert_record(rec)
            links = list(rec.get("linked_user_ids") or [])
            if other not in links:
                links.append(other)
            with get_business_db_session() as session:
                session.execute(
                    text(
                        """
                        UPDATE identities
                        SET linked_user_ids = CAST(:links AS jsonb), updated_at = CAST(:now AS timestamptz)
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": uid, "links": json.dumps(links, ensure_ascii=False), "now": now},
                )

    def expand_visible_user_ids(self, user_id: str) -> Set[str]:
        if not user_id:
            return set()
        data = {item["user_id"]: item for item in self.list_identities()}
        out: Set[str] = {user_id}
        rec = data.get(user_id)
        if rec:
            out.update(str(x).strip() for x in (rec.get("linked_user_ids") or []) if str(x).strip())
        for uid, r in data.items():
            linked = [str(x).strip() for x in (r.get("linked_user_ids") or []) if str(x).strip()]
            if user_id in linked:
                out.add(str(uid))
                out.update(linked)
        return out

    def _insert_record(self, record: dict) -> None:
        with get_business_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO identities (
                        user_id, username, nickname, role, source, linked_user_ids, created_at, updated_at
                    ) VALUES (
                        :user_id, :username, :nickname, :role, :source,
                        CAST(:linked_user_ids AS jsonb), CAST(:created_at AS timestamptz),
                        CAST(:updated_at AS timestamptz)
                    )
                    ON CONFLICT (user_id) DO NOTHING
                    """
                ),
                {**record, "linked_user_ids": json.dumps(record.get("linked_user_ids") or [], ensure_ascii=False)},
            )

    @staticmethod
    def _from_row(row) -> dict:
        def _iso(v):
            return v.astimezone(timezone.utc).isoformat() if isinstance(v, datetime) else str(v or "")

        data = dict(row)
        data["role"] = normalize_role(data.get("role") or "analyst")
        data["created_at"] = _iso(data.get("created_at"))
        data["updated_at"] = _iso(data.get("updated_at"))
        data["linked_user_ids"] = list(data.get("linked_user_ids") or [])
        return data


_default_identity_store = SqlAlchemyIdentityStore()


def get_identity_store() -> SqlAlchemyIdentityStore:
    return _default_identity_store

