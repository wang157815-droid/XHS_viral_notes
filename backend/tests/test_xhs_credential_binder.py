"""Phase 2-A: XhsCredentialBinder 单元测试。

借助 fake AuthOrchestrator 完全跳过 selfinfo 真实调用。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from backend.app.services.xhs_auth import (
    BindResult,
    XhsCredentialBinder,
    XhsCredentialStore,
)


class FakeOrchestrator:
    """模拟 AuthOrchestrator 的两个 public 入口。"""

    def __init__(
        self,
        identity: Optional[Dict[str, Any]] = None,
        cookies_for_session: Optional[Dict[str, str]] = None,
        raise_on_extract: Optional[Exception] = None,
        raise_on_session: Optional[Exception] = None,
    ) -> None:
        self._identity = identity
        self._sessions = cookies_for_session or {}
        self._raise_extract = raise_on_extract
        self._raise_session = raise_on_session
        self.extract_calls: list[str] = []
        self.session_calls: list[str] = []

    # 同步方法（被 asyncio.to_thread 包裹）
    def extract_xhs_identity_from_cookies(self, cookies_str: str):
        self.extract_calls.append(cookies_str)
        if self._raise_extract:
            raise self._raise_extract
        return self._identity

    async def get_session_cookies_str(self, session_id: str):
        self.session_calls.append(session_id)
        if self._raise_session:
            raise self._raise_session
        return self._sessions.get(session_id)


@pytest.fixture()
def store(tmp_path) -> XhsCredentialStore:
    return XhsCredentialStore(store_file=str(tmp_path / "xhs.json"))


def _make_binder(store: XhsCredentialStore, **fake_kwargs) -> XhsCredentialBinder:
    return XhsCredentialBinder(store=store, orchestrator=FakeOrchestrator(**fake_kwargs))


# ---------------------------------------------------------------------------
# bind_with_cookies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_with_cookies_success(store: XhsCredentialStore):
    binder = _make_binder(
        store,
        identity={
            "user_id": "5e8c7b001",
            "nickname": "测试小红书号",
            "username": "xhs_5e8c7b001",
            "role": "user",
            "source": "selfinfo",
        },
    )

    result: BindResult = await binder.bind_with_cookies(
        "u_alice", "a1=cookie; web_session=ABC"
    )
    assert result.success is True
    assert result.xhs_user_id == "5e8c7b001"
    assert result.xhs_nickname == "测试小红书号"
    assert result.cookies_path == "datas/users/xhs_5e8c7b001/cookies.json"

    cred = store.get_by_redmuse_user_id("u_alice")
    assert cred is not None
    assert cred.cookies_path == "datas/users/xhs_5e8c7b001/cookies.json"
    assert cred.status == "active"
    assert cred.xhs_user_id == "5e8c7b001"


@pytest.mark.asyncio
async def test_bind_with_cookies_empty_redmuse_user(store: XhsCredentialStore):
    binder = _make_binder(store, identity={"user_id": "x", "nickname": "y", "username": "xhs_x"})
    result = await binder.bind_with_cookies("", "abc")
    assert not result.success
    assert result.error_code == "invalid_redmuse_user"


@pytest.mark.asyncio
async def test_bind_with_cookies_empty_cookies(store: XhsCredentialStore):
    binder = _make_binder(store, identity=None)
    result = await binder.bind_with_cookies("u_a", "  ")
    assert not result.success
    assert result.error_code == "empty_cookies"


@pytest.mark.asyncio
async def test_bind_with_cookies_selfinfo_returns_none(store: XhsCredentialStore):
    binder = _make_binder(store, identity=None)
    result = await binder.bind_with_cookies("u_a", "a1=valid_format_but_unauthed")
    assert not result.success
    assert result.error_code == "selfinfo_invalid"
    assert store.get_by_redmuse_user_id("u_a") is None


@pytest.mark.asyncio
async def test_bind_with_cookies_selfinfo_raises(store: XhsCredentialStore):
    binder = _make_binder(store, raise_on_extract=RuntimeError("network down"))
    result = await binder.bind_with_cookies("u_a", "a1=cookie")
    assert not result.success
    assert result.error_code == "selfinfo_error"
    assert "network down" in result.error_message


@pytest.mark.asyncio
async def test_bind_with_cookies_identity_incomplete(store: XhsCredentialStore):
    """identity 字典字段缺失时，binder 必须拒绝（防止脏数据写入 store）。"""
    binder = _make_binder(
        store,
        identity={"user_id": "", "nickname": "no_id", "username": "xhs_blank"},
    )
    result = await binder.bind_with_cookies("u_a", "a1=cookie")
    assert not result.success
    assert result.error_code == "identity_incomplete"


@pytest.mark.asyncio
async def test_bind_with_cookies_overwrites_existing(store: XhsCredentialStore):
    """同一 RedMuse 用户重新绑定不同 XHS 账号时，更新而非堆叠记录。"""
    store.upsert(
        redmuse_user_id="u_a",
        cookies_path="datas/users/xhs_old/cookies.json",
        xhs_user_id="old_id",
        status="expired",
    )
    binder = _make_binder(
        store,
        identity={"user_id": "new_id", "nickname": "新账号", "username": "xhs_new"},
    )
    result = await binder.bind_with_cookies("u_a", "a1=fresh")
    assert result.success
    cred = store.get_by_redmuse_user_id("u_a")
    assert cred.xhs_user_id == "new_id"
    assert cred.status == "active"
    assert store.count() == 1


# ---------------------------------------------------------------------------
# bind_from_qr_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_from_qr_session_success(store: XhsCredentialStore):
    binder = _make_binder(
        store,
        identity={"user_id": "qr_uid", "nickname": "扫码", "username": "xhs_qr"},
        cookies_for_session={"sess_1": "a1=qr_cookie"},
    )
    result = await binder.bind_from_qr_session("u_b", "sess_1")
    assert result.success
    assert result.xhs_user_id == "qr_uid"
    assert binder.orchestrator.session_calls == ["sess_1"]
    assert binder.orchestrator.extract_calls == ["a1=qr_cookie"]


@pytest.mark.asyncio
async def test_bind_from_qr_session_not_ready(store: XhsCredentialStore):
    binder = _make_binder(store, cookies_for_session={})
    result = await binder.bind_from_qr_session("u_b", "missing")
    assert not result.success
    assert result.error_code == "session_not_ready"


@pytest.mark.asyncio
async def test_bind_from_qr_session_missing_args(store: XhsCredentialStore):
    binder = _make_binder(store)
    result = await binder.bind_from_qr_session("", "")
    assert not result.success
    assert result.error_code == "invalid_arguments"


@pytest.mark.asyncio
async def test_bind_from_qr_session_orchestrator_raises(store: XhsCredentialStore):
    binder = _make_binder(store, raise_on_session=RuntimeError("session backend down"))
    result = await binder.bind_from_qr_session("u_b", "sess_x")
    assert not result.success
    assert result.error_code == "session_error"
