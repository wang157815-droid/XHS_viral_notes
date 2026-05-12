"""Phase 1: /xhs-auth/credential* 路由集成测试。"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


API = "/api/v1"


@pytest.fixture()
def client() -> TestClient:
    from backend.app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


@pytest.fixture()
def admin_token(client: TestClient) -> Dict[str, Any]:
    """seed admin → login 拿 token。"""
    from backend.app.services.redmuse_auth import get_user_store

    store = get_user_store()
    store.create_user(
        username="rootadmin",
        password="adminpw1",
        nickname="超管",
        role="admin",
    )
    r = client.post(
        f"{API}/auth/login", json={"username": "rootadmin", "password": "adminpw1"}
    )
    assert r.status_code == 200, r.json()
    body = r.json()["data"]
    return {"token": body["token"], "user_id": body["user"]["user_id"]}


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_get_credential_when_not_bound(client: TestClient, admin_token):
    r = client.get(f"{API}/xhs-auth/credential", headers=_bearer(admin_token["token"]))
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["redmuse_user_id"] == admin_token["user_id"]
    assert body["is_bound"] is False
    assert body["status"] == "unbound"


def test_get_credential_when_bound(client: TestClient, admin_token):
    from backend.app.services.xhs_auth import get_credential_store

    get_credential_store().upsert(
        redmuse_user_id=admin_token["user_id"],
        cookies_path="datas/users/admin/cookies.json",
        xhs_user_id="6411xhs",
        xhs_nickname="管理员小红书号",
        status="active",
        status_message="ok",
    )

    r = client.get(f"{API}/xhs-auth/credential", headers=_bearer(admin_token["token"]))
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["is_bound"] is True
    assert body["xhs_user_id"] == "6411xhs"
    assert body["status"] == "active"
    # 不暴露 cookies_path
    assert "cookies_path" not in body


def test_get_credential_requires_auth(client: TestClient):
    r = client.get(f"{API}/xhs-auth/credential")
    assert r.status_code == 401


def test_unbind_when_bound(client: TestClient, admin_token):
    from backend.app.services.xhs_auth import get_credential_store

    get_credential_store().upsert(
        redmuse_user_id=admin_token["user_id"],
        cookies_path="datas/users/admin/cookies.json",
    )
    r = client.delete(
        f"{API}/xhs-auth/credential", headers=_bearer(admin_token["token"])
    )
    assert r.status_code == 200
    assert r.json()["data"]["unbound"] is True
    assert get_credential_store().get_by_redmuse_user_id(admin_token["user_id"]) is None


def test_unbind_when_not_bound(client: TestClient, admin_token):
    r = client.delete(
        f"{API}/xhs-auth/credential", headers=_bearer(admin_token["token"])
    )
    assert r.status_code == 404


def test_admin_lists_all_credentials(client: TestClient, admin_token):
    from backend.app.services.redmuse_auth import get_user_store
    from backend.app.services.xhs_auth import get_credential_store

    other = get_user_store().create_user(username="alice", password="pw1234")

    cred_store = get_credential_store()
    cred_store.upsert(
        redmuse_user_id=admin_token["user_id"],
        cookies_path="datas/users/admin/cookies.json",
        status="active",
    )
    cred_store.upsert(
        redmuse_user_id=other.user_id,
        cookies_path="datas/users/alice/cookies.json",
        status="expired",
    )

    r = client.get(
        f"{API}/xhs-auth/credentials", headers=_bearer(admin_token["token"])
    )
    assert r.status_code == 200
    creds = r.json()["data"]["credentials"]
    by_uid = {c["redmuse_user_id"]: c for c in creds}
    assert by_uid[admin_token["user_id"]]["status"] == "active"
    assert by_uid[other.user_id]["status"] == "expired"


def test_list_credentials_requires_admin(client: TestClient):
    from backend.app.services.redmuse_auth import get_user_store

    get_user_store().create_user(username="ord", password="pw1234")
    login = client.post(
        f"{API}/auth/login", json={"username": "ord", "password": "pw1234"}
    )
    token = login.json()["data"]["token"]
    r = client.get(f"{API}/xhs-auth/credentials", headers=_bearer(token))
    assert r.status_code == 403


def test_credential_status_for_unbound(client: TestClient, admin_token):
    r = client.get(
        f"{API}/xhs-auth/credential/status", headers=_bearer(admin_token["token"])
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["is_bound"] is False
    assert body["status"] == "unbound"


def test_credential_status_force_updates_store_and_returns_credential(
    client: TestClient, admin_token, monkeypatch
):
    from backend.app.services.xhs_auth import get_credential_store

    store = get_credential_store()
    store.upsert(
        redmuse_user_id=admin_token["user_id"],
        cookies_path="datas/users/admin/cookies.json",
        xhs_user_id="6411xhs",
        xhs_nickname="管理员小红书号",
        status="unknown",
    )

    monkeypatch.setattr(
        "backend.app.services.cookie_health_service.cookie_health_service.get_cookie_health",
        lambda **kwargs: {
            "status": "valid",
            "message": "Cookie 有效",
            "last_checked_at": "2026-05-09T08:30:00+00:00",
        },
    )

    r = client.get(
        f"{API}/xhs-auth/credential/status?force=true",
        headers=_bearer(admin_token["token"]),
    )
    assert r.status_code == 200, r.json()
    body = r.json()["data"]
    assert body["status"] == "active"
    assert body["message"] == "Cookie 有效"
    assert body["credential"]["status"] == "active"
    assert body["credential"]["xhs_user_id"] == "6411xhs"
    assert "cookies_path" not in body["credential"]
