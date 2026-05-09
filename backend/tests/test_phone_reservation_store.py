"""PhoneReservationStore 单测：CRUD、复用判断、JSON 持久化往返。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.xhs_auth import (
    PhoneReservation,
    PhoneReservationStore,
)


def _store(tmp_path, *, window_sec: int = 1200) -> PhoneReservationStore:
    return PhoneReservationStore(
        store_file=str(tmp_path / "reservations.json"),
        reuse_window_sec=window_sec,
    )


def _make_reservation(
    user_id: str = "u1",
    *,
    sms_received: bool = False,
    seen_codes=None,
    purchased_at: str | None = None,
    purchased_at_monotonic: float | None = None,
) -> PhoneReservation:
    return PhoneReservation(
        redmuse_user_id=user_id,
        order_id="order_1",
        phone="85211112222",
        country_code="HK",
        sms_received=sms_received,
        seen_codes=list(seen_codes or []),
        purchased_at=purchased_at or datetime.now(timezone.utc).isoformat(),
        purchased_at_monotonic=(
            purchased_at_monotonic
            if purchased_at_monotonic is not None
            else time.monotonic()
        ),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_save_then_get_roundtrip(tmp_path):
    store = _store(tmp_path)
    res = _make_reservation()
    store.save(res)

    fetched = store.get("u1")
    assert fetched is not None
    assert fetched.order_id == "order_1"
    assert fetched.phone == "85211112222"
    assert fetched.country_code == "HK"
    assert fetched.sms_received is False


def test_save_replaces_existing_for_same_user(tmp_path):
    store = _store(tmp_path)
    store.save(_make_reservation(user_id="u_same"))
    res2 = _make_reservation(user_id="u_same")
    res2.order_id = "order_2"
    res2.phone = "85299998888"
    store.save(res2)
    fetched = store.get("u_same")
    assert fetched is not None
    assert fetched.order_id == "order_2"
    assert fetched.phone == "85299998888"
    # 仅一条
    assert len(store.list_all()) == 1


def test_get_unknown_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.get("nope") is None
    assert store.get("") is None


def test_delete_removes_entry(tmp_path):
    store = _store(tmp_path)
    store.save(_make_reservation())
    assert store.delete("u1") is True
    assert store.get("u1") is None
    assert store.delete("u1") is False  # 已不存在


def test_persistence_across_instances(tmp_path):
    store1 = _store(tmp_path)
    store1.save(_make_reservation(user_id="u_persist"))
    store2 = _store(tmp_path)
    fetched = store2.get("u_persist")
    assert fetched is not None
    assert fetched.order_id == "order_1"


def test_corrupt_json_treated_as_empty(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text("{not json", encoding="utf-8")
    store = PhoneReservationStore(store_file=str(path))
    assert store.list_all() == []


# ---------------------------------------------------------------------------
# mark_sms_received / add_seen_code
# ---------------------------------------------------------------------------


def test_mark_sms_received_sets_flag_and_appends_seen_code(tmp_path):
    store = _store(tmp_path)
    store.save(_make_reservation())
    updated = store.mark_sms_received("u1", code="999111")
    assert updated is not None
    assert updated.sms_received is True
    assert "999111" in updated.seen_codes

    # 重复 mark 不重复加 code
    again = store.mark_sms_received("u1", code="999111")
    assert again is not None
    assert again.seen_codes.count("999111") == 1


def test_add_seen_code_only_for_existing_user(tmp_path):
    store = _store(tmp_path)
    assert store.add_seen_code("ghost", "111") is None
    store.save(_make_reservation())
    updated = store.add_seen_code("u1", "111")
    assert updated is not None
    assert "111" in updated.seen_codes
    # 之前没收过 code 不应改变 sms_received（仅 add_seen_code）
    assert updated.sms_received is False


# ---------------------------------------------------------------------------
# is_reusable / get_reusable
# ---------------------------------------------------------------------------


def test_get_reusable_returns_when_within_window_and_not_received(tmp_path):
    store = _store(tmp_path, window_sec=1200)
    store.save(_make_reservation())
    assert store.get_reusable("u1") is not None


def test_get_reusable_none_when_sms_received(tmp_path):
    store = _store(tmp_path, window_sec=1200)
    store.save(_make_reservation(sms_received=True))
    assert store.get_reusable("u1") is None


def test_get_reusable_none_when_aged_out(tmp_path):
    """伪造一个 30 分钟前的 reservation，window=1200 (20 分钟) 应判为不可用。"""
    store = _store(tmp_path, window_sec=20 * 60)
    old_iso = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    store.save(
        _make_reservation(
            purchased_at=old_iso,
            purchased_at_monotonic=0.0,  # 走 wall-clock 回退
        )
    )
    assert store.get_reusable("u1") is None


def test_get_reusable_none_when_window_zero(tmp_path):
    """window=0 表示禁用复用。"""
    store = _store(tmp_path, window_sec=0)
    store.save(_make_reservation())
    assert store.get_reusable("u1") is None


# ---------------------------------------------------------------------------
# 文件结构兼容
# ---------------------------------------------------------------------------


def test_save_writes_expected_json_layout(tmp_path):
    store = _store(tmp_path)
    res = _make_reservation()
    store.save(res)
    raw = json.loads(
        (tmp_path / "reservations.json").read_text(encoding="utf-8")
    )
    assert raw["version"] == 1
    assert isinstance(raw["reservations"], list)
    assert len(raw["reservations"]) == 1
    item = raw["reservations"][0]
    assert item["redmuse_user_id"] == "u1"
    assert item["order_id"] == "order_1"
    assert item["phone"] == "85211112222"
    assert item["sms_received"] is False
    assert item["seen_codes"] == []
