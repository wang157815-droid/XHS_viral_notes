"""Phase 2-A: /xhs-auth/bind/* 路由集成测试。

mock 掉 selfinfo 网络调用，专注路由 → binder → store 的端到端链路。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient


API = "/api/v1"


class FakeOrchestrator:
    def __init__(
        self,
        identity: Optional[Dict[str, Any]] = None,
        cookies_for_session: Optional[Dict[str, str]] = None,
    ) -> None:
        self._identity = identity
        self._sessions = cookies_for_session or {}

    def extract_xhs_identity_from_cookies(self, cookies_str: str):
        return self._identity

    async def get_session_cookies_str(self, session_id: str):
        return self._sessions.get(session_id)


@pytest.fixture()
def client_with_auth(monkeypatch):
    """启动 FastAPI app，注册 admin 用户，注入 fake orchestrator 到 binder。"""
    from backend.app.main import app
    from backend.app.services.redmuse_auth import get_user_store
    from backend.app.services.xhs_auth import credential_binder as binder_mod

    app.dependency_overrides.clear()
    client = TestClient(app)

    store = get_user_store()
    store.create_user(username="phase2admin", password="adminpw1", role="admin")
    login = client.post(
        f"{API}/auth/login",
        json={"username": "phase2admin", "password": "adminpw1"},
    )
    body = login.json()["data"]
    token = body["token"]
    user_id = body["user"]["user_id"]

    # 注入 fake orchestrator：通过替换 binder 单例
    fake = FakeOrchestrator(
        identity={"user_id": "5e8c7b", "nickname": "测试号", "username": "xhs_5e8c7b"},
        cookies_for_session={"sess_ok": "a1=qr"},
    )
    isolated_binder = binder_mod.XhsCredentialBinder(orchestrator=fake)
    monkeypatch.setattr(binder_mod, "_default_binder", isolated_binder, raising=False)

    return {
        "client": client,
        "token": token,
        "user_id": user_id,
        "fake": fake,
    }


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_bind_cookies_success(client_with_auth):
    ctx = client_with_auth
    r = ctx["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": "a1=valid; web_session=ABC"},
        headers=_bearer(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["success"] is True
    assert body["xhs_user_id"] == "5e8c7b"
    assert body["credential"]["is_bound"] is True

    # 紧接着 GET /credential 应该看到 active
    r2 = ctx["client"].get(
        f"{API}/xhs-auth/credential", headers=_bearer(ctx["token"])
    )
    cred = r2.json()["data"]
    assert cred["is_bound"] is True
    assert cred["xhs_user_id"] == "5e8c7b"
    assert cred["status"] == "active"


def test_bind_cookies_requires_auth(client_with_auth):
    r = client_with_auth["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": "anything"},
    )
    assert r.status_code == 401


def test_bind_cookies_invalid_selfinfo(client_with_auth, monkeypatch):
    """selfinfo 返回 None → 400 + selfinfo_invalid。"""
    from backend.app.services.xhs_auth import credential_binder as binder_mod

    bad_orch = FakeOrchestrator(identity=None)
    monkeypatch.setattr(
        binder_mod,
        "_default_binder",
        binder_mod.XhsCredentialBinder(orchestrator=bad_orch),
        raising=False,
    )

    ctx = client_with_auth
    r = ctx["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": "a1=invalid"},
        headers=_bearer(ctx["token"]),
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "selfinfo_invalid"


def test_bind_from_session_success(client_with_auth):
    ctx = client_with_auth
    r = ctx["client"].post(
        f"{API}/xhs-auth/bind/from-session",
        json={"session_id": "sess_ok"},
        headers=_bearer(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["success"] is True
    assert body["xhs_user_id"] == "5e8c7b"


def test_bind_from_session_not_ready_returns_409(client_with_auth):
    ctx = client_with_auth
    r = ctx["client"].post(
        f"{API}/xhs-auth/bind/from-session",
        json={"session_id": "sess_does_not_exist"},
        headers=_bearer(ctx["token"]),
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "session_not_ready"


def test_bind_cookies_validates_payload(client_with_auth):
    ctx = client_with_auth
    r = ctx["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": ""},
        headers=_bearer(ctx["token"]),
    )
    assert r.status_code == 422  # pydantic min_length=1


def test_bind_overwrites_existing_credential(client_with_auth, monkeypatch):
    """同一 RedMuse 用户连续两次绑定不同 XHS 账号，store 中只剩最后一条。"""
    from backend.app.services.xhs_auth import credential_binder as binder_mod
    from backend.app.services.xhs_auth import get_credential_store

    ctx = client_with_auth

    # 第一次绑：使用 fixture 默认 fake → user_id=5e8c7b
    r1 = ctx["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": "a1=first"},
        headers=_bearer(ctx["token"]),
    )
    assert r1.status_code == 200

    # 切换 binder fake → 第二次返回另一个 xhs_user_id
    new_fake = FakeOrchestrator(
        identity={"user_id": "newxhs", "nickname": "另一个号", "username": "xhs_newxhs"}
    )
    monkeypatch.setattr(
        binder_mod,
        "_default_binder",
        binder_mod.XhsCredentialBinder(orchestrator=new_fake),
        raising=False,
    )

    r2 = ctx["client"].post(
        f"{API}/xhs-auth/bind/cookies",
        json={"cookies_str": "a1=second"},
        headers=_bearer(ctx["token"]),
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["xhs_user_id"] == "newxhs"

    creds = get_credential_store().list_credentials()
    assert len(creds) == 1
    assert creds[0].xhs_user_id == "newxhs"
