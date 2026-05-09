"""虚拟手机号短期复用记录（Phase 3 重构补丁）。

背景：hero-sms 购号成功就开始计费。如果 SmsLoginService 在「填手机号」之后
任何一步失败（DOM selector 失配、滑块、风控等），用户重试会再次调
``acquire_phone`` → 重复扣费。但 hero-sms 的虚拟号在 20 分钟内仍然有效，
**只要还没在这个号上用过任何验证码**，复用同一号 + 重发 SMS 比新购号划算。

策略：
- 每个 RedMuse 用户一条 reservation；
- 字段：``purchase``（PhonePurchase）、``purchased_at`` (utc iso)、
  ``purchased_at_monotonic``（time.monotonic 进程内时钟，避免时钟跳变）、
  ``sms_received``（True 表示这号上已经收过验证码，**禁止复用**）、
  ``seen_codes``（之前已经出现过的验证码集合，给 ``wait_sms_code`` 用于过滤旧码）。
- 复用判断条件：``not sms_received`` 且 ``elapsed < window_sec``。

终态规则：
- ``success``：reservation 删除（cookies 已落地，号没意义）
- ``cancelled``（用户主动取消）：reservation 保留
- ``expired`` / ``error``：reservation 保留（如果 sms_received=False 可下次复用）

并发：单进程 RLock + 原子写。多进程留给后续 DB 迁移。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .sms_provider import PhonePurchase


_DEFAULT_STORE_FILE = "datas/redmuse_auth/sms_phone_reservations.json"
_STORE_VERSION = 1
_DEFAULT_REUSE_WINDOW_SEC = 20 * 60  # 20 分钟（hero-sms 默认号码生命周期）


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PhoneReservation:
    """一个 RedMuse 用户当前持有的、尚可复用的虚拟号记录。"""

    redmuse_user_id: str
    order_id: str
    phone: str
    country_code: str = ""
    purchased_at: str = field(default_factory=_now_iso)
    """购买时间（UTC ISO 文本，跨进程仍可比较）。"""

    purchased_at_monotonic: float = 0.0
    """进程启动后单调时间（仅本进程内可信；跨进程恢复后回退到 ``purchased_at``）。"""

    sms_received: bool = False
    """关键标志：曾经在此号上拿到过任意验证码 → 不可复用。"""

    seen_codes: List[str] = field(default_factory=list)
    """历史已见过的验证码（用于 ``wait_sms_code(seen_codes=...)`` 过滤旧码）。"""

    raw: Dict[str, Any] = field(default_factory=dict)
    """购号时的原始响应，调试用。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "redmuse_user_id": self.redmuse_user_id,
            "order_id": self.order_id,
            "phone": self.phone,
            "country_code": self.country_code,
            "purchased_at": self.purchased_at,
            "purchased_at_monotonic": self.purchased_at_monotonic,
            "sms_received": self.sms_received,
            "seen_codes": list(self.seen_codes),
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhoneReservation":
        return cls(
            redmuse_user_id=str(data["redmuse_user_id"]),
            order_id=str(data.get("order_id") or ""),
            phone=str(data.get("phone") or ""),
            country_code=str(data.get("country_code") or ""),
            purchased_at=str(data.get("purchased_at") or _now_iso()),
            purchased_at_monotonic=float(data.get("purchased_at_monotonic") or 0.0),
            sms_received=bool(data.get("sms_received") or False),
            seen_codes=[str(c) for c in (data.get("seen_codes") or [])],
            raw=dict(data.get("raw") or {}),
        )

    def to_purchase(self) -> PhonePurchase:
        return PhonePurchase(
            order_id=self.order_id,
            phone=self.phone,
            country_code=self.country_code,
            raw=dict(self.raw),
        )

    def age_seconds(self, *, now_monotonic: Optional[float] = None) -> float:
        """计算 reservation 年龄（秒）。

        统一用 wall-clock (``purchased_at`` ISO 文本)。早期版本曾优先用
        ``time.monotonic()`` 计算同进程精确差值，但 ``monotonic`` 跨进程会重置 →
        进程 A 写入 monotonic=100，进程 B 启动后 ``time.monotonic()=5``，
        会算出 age=0 → 错误地把 30min 前的号判成"可复用"。

        ``now_monotonic`` 形参保留是为了向后兼容老调用方，已无实际作用。
        系统时间被改时可能误判，但对 20min 量级影响很小，且即使误判
        也是保守地把"原本可复用"判成"不可用"，会触发新购号，不会造成损失。
        """
        try:
            purchased_dt = datetime.fromisoformat(self.purchased_at)
        except ValueError:
            return float("inf")
        if purchased_dt.tzinfo is None:
            purchased_dt = purchased_dt.replace(tzinfo=timezone.utc)
        return max(
            0.0, (datetime.now(timezone.utc) - purchased_dt).total_seconds()
        )

    def is_reusable(self, *, window_sec: float) -> bool:
        if self.sms_received:
            return False
        if not self.order_id or not self.phone:
            return False
        return self.age_seconds() < window_sec

    def reusable_reason(self, *, window_sec: float) -> str:
        """诊断用：为什么这条 reservation 可 / 不可复用，给日志写明细。"""
        if self.sms_received:
            return f"已收过验证码 (seen_codes={len(self.seen_codes)})"
        if not self.order_id or not self.phone:
            return "数据残缺 (缺 order_id 或 phone)"
        age = self.age_seconds()
        if age >= window_sec:
            return f"超出复用窗口 (age={int(age)}s >= window={int(window_sec)}s)"
        return f"可复用 (age={int(age)}s < window={int(window_sec)}s, sms_received=False)"


class PhoneReservationStore:
    """JSON 文件实现的虚拟号 reservation 表。"""

    def __init__(
        self,
        store_file: str = _DEFAULT_STORE_FILE,
        *,
        reuse_window_sec: int = _DEFAULT_REUSE_WINDOW_SEC,
    ) -> None:
        self.path = Path(store_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reuse_window_sec = max(0, int(reuse_window_sec))
        self._lock = threading.RLock()

    # ----- 内部 IO -----
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": _STORE_VERSION, "reservations": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": _STORE_VERSION, "reservations": []}
        if not isinstance(data, dict):
            return {"version": _STORE_VERSION, "reservations": []}
        if not isinstance(data.get("reservations"), list):
            data["reservations"] = []
        data.setdefault("version", _STORE_VERSION)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----- 查询 -----
    def get(self, redmuse_user_id: str) -> Optional[PhoneReservation]:
        target = (redmuse_user_id or "").strip()
        if not target:
            return None
        with self._lock:
            for item in self._load().get("reservations", []):
                if str(item.get("redmuse_user_id")) == target:
                    return PhoneReservation.from_dict(item)
        return None

    def get_reusable(self, redmuse_user_id: str) -> Optional[PhoneReservation]:
        """符合复用条件就返回；否则 None。不会自动清理过期记录（手动 ``delete``）。"""
        existing = self.get(redmuse_user_id)
        if existing is None:
            return None
        if existing.is_reusable(window_sec=self.reuse_window_sec):
            return existing
        return None

    def list_all(self) -> List[PhoneReservation]:
        with self._lock:
            return [
                PhoneReservation.from_dict(item)
                for item in self._load().get("reservations", [])
            ]

    # ----- 写入 -----
    def save(self, reservation: PhoneReservation) -> PhoneReservation:
        if not reservation.redmuse_user_id:
            raise ValueError("redmuse_user_id 不能为空")
        with self._lock:
            data = self._load()
            entries: List[Dict[str, Any]] = list(data.get("reservations") or [])
            replaced = False
            for idx, item in enumerate(entries):
                if str(item.get("redmuse_user_id")) == reservation.redmuse_user_id:
                    entries[idx] = reservation.to_dict()
                    replaced = True
                    break
            if not replaced:
                entries.append(reservation.to_dict())
            data["reservations"] = entries
            self._save(data)
        return reservation

    def mark_sms_received(
        self,
        redmuse_user_id: str,
        *,
        code: Optional[str] = None,
    ) -> Optional[PhoneReservation]:
        """记一笔验证码已到达。``code`` 会被加进 ``seen_codes`` 以便后续复用号
        重发短信时由 ``wait_sms_code`` 过滤旧码。"""
        with self._lock:
            data = self._load()
            entries: List[Dict[str, Any]] = list(data.get("reservations") or [])
            for idx, item in enumerate(entries):
                if str(item.get("redmuse_user_id")) == redmuse_user_id:
                    item["sms_received"] = True
                    if code:
                        seen: List[str] = list(item.get("seen_codes") or [])
                        if code not in seen:
                            seen.append(code)
                        item["seen_codes"] = seen
                    entries[idx] = item
                    data["reservations"] = entries
                    self._save(data)
                    return PhoneReservation.from_dict(item)
        return None

    def add_seen_code(
        self,
        redmuse_user_id: str,
        code: str,
    ) -> Optional[PhoneReservation]:
        """单独加一条 seen_code，不修改 ``sms_received``（一般用不到，
        ``mark_sms_received`` 已涵盖）。"""
        if not code:
            return None
        with self._lock:
            data = self._load()
            entries: List[Dict[str, Any]] = list(data.get("reservations") or [])
            for idx, item in enumerate(entries):
                if str(item.get("redmuse_user_id")) == redmuse_user_id:
                    seen: List[str] = list(item.get("seen_codes") or [])
                    if code not in seen:
                        seen.append(code)
                        item["seen_codes"] = seen
                    entries[idx] = item
                    data["reservations"] = entries
                    self._save(data)
                    return PhoneReservation.from_dict(item)
        return None

    def delete(self, redmuse_user_id: str) -> bool:
        with self._lock:
            data = self._load()
            entries: List[Dict[str, Any]] = list(data.get("reservations") or [])
            new_list = [
                item
                for item in entries
                if str(item.get("redmuse_user_id")) != redmuse_user_id
            ]
            if len(new_list) == len(entries):
                return False
            data["reservations"] = new_list
            self._save(data)
            return True


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


_default_store: Optional[PhoneReservationStore] = None
_default_store_lock = threading.Lock()


def get_phone_reservation_store() -> PhoneReservationStore:
    """单例访问器；conftest 用 :func:`set_default_store` 替换以隔离测试。"""
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = PhoneReservationStore()
    return _default_store


def set_default_store(store: Optional[PhoneReservationStore]) -> None:
    """测试钩子：注入隔离 store。"""
    global _default_store
    _default_store = store
