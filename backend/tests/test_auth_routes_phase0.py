"""Phase 0: /auth/login + /auth/me + /auth/users 端到端契约测试。"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


API = "/api/v1"


@pytest.fixture()
def client() -> TestClient:
    """每个测试拿到一个干净的 TestClient + 已隔离的 user store（来自 conftest）。"""
    from backend.app.main import app

    # 清理可能由其它测试残留的 dependency_overrides
    app.dependency_overrides.clear()
    return TestClient(app)


@pytest.fixture()
def admin_user_id(client: TestClient) -> str:
    """seed 一个 admin 账号，返回 user_id。"""
    from backend.app.services.redmuse_auth import get_user_store

    store = get_user_store()
    user = store.create_user(
        username="rootadmin",
        password="adminpw1",
        nickname="超管",
        role="admin",
        xhs_credential_path="datas/users/admin/cookies.json",
    )
    return user.user_id


def _login(client: TestClient, username: str, password: str) -> Dict[str, Any]:
    r = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    return {"status": r.status_code, "json": r.json()}


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


def test_login_success_returns_token_and_user(client: TestClient, admin_user_id: str):
    r = _login(client, "rootadmin", "adminpw1")
    assert r["status"] == 200, r["json"]
    body = r["json"]
    assert body["ok"] is True
    assert body["data"]["token"]
    assert body["data"]["user"]["user_id"] == admin_user_id
    assert body["data"]["user"]["role"] == "admin"
    # password_hash 不能泄漏
    assert "password_hash" not in body["data"]["user"]


def test_login_username_case_insensitive(client: TestClient, admin_user_id: str):
    r = _login(client, "RootAdmin", "adminpw1")
    assert r["status"] == 200
    assert r["json"]["data"]["user"]["user_id"] == admin_user_id


def test_login_wrong_password(client: TestClient, admin_user_id: str):
    r = _login(client, "rootadmin", "wrong")
    assert r["status"] == 401
    assert "用户名或密码错误" in r["json"]["error"]["message"]


def test_login_unknown_user(client: TestClient):
    r = _login(client, "ghost", "anything")
    assert r["status"] == 401


def test_login_disabled_account(client: TestClient, admin_user_id: str):
    from backend.app.services.redmuse_auth import get_user_store

    get_user_store().set_status(admin_user_id, "disabled")
    r = _login(client, "rootadmin", "adminpw1")
    assert r["status"] == 401  # disabled 时 authenticate 直接返回 None


# ---------------------------------------------------------------------------
# /auth/me + token_type
# ---------------------------------------------------------------------------


def test_me_returns_token_type_redmuse(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    r = client.get(f"{API}/auth/me", headers=_bearer(token))
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["user_id"] == admin_user_id
    assert body["token_type"] == "redmuse"
    assert body["role"] == "admin"


def test_me_without_token_is_401(client: TestClient):
    r = client.get(f"{API}/auth/me")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/users (admin 专用)
# ---------------------------------------------------------------------------


def test_create_user_requires_admin(client: TestClient, admin_user_id: str):
    # 先创建一个普通用户
    from backend.app.services.redmuse_auth import get_user_store

    plain = get_user_store().create_user(username="alice", password="pw1234")
    plain_token = _login(client, "alice", "pw1234")["json"]["data"]["token"]

    r = client.post(
        f"{API}/auth/users",
        headers=_bearer(plain_token),
        json={"username": "newby", "password": "pw1234"},
    )
    assert r.status_code == 403


def test_admin_can_create_and_list_users(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]

    create = client.post(
        f"{API}/auth/users",
        headers=_bearer(token),
        json={
            "username": "writer1",
            "password": "writerpw1",
            "nickname": "运营A",
            "role": "analyst",
        },
    )
    assert create.status_code == 200, create.json()
    new_user = create.json()["data"]
    assert new_user["username"] == "writer1"
    assert new_user["role"] == "analyst"
    assert "password_hash" not in new_user

    listing = client.get(f"{API}/auth/users", headers=_bearer(token))
    assert listing.status_code == 200
    usernames = [u["username"] for u in listing.json()["data"]["users"]]
    assert {"rootadmin", "writer1"}.issubset(usernames)

    # 新用户能登录
    r = _login(client, "writer1", "writerpw1")
    assert r["status"] == 200


def test_create_user_duplicate_username_returns_409(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    first = client.post(
        f"{API}/auth/users",
        headers=_bearer(token),
        json={"username": "dup", "password": "pw1234"},
    )
    assert first.status_code == 200
    second = client.post(
        f"{API}/auth/users",
        headers=_bearer(token),
        json={"username": "DUP", "password": "pw5678"},
    )
    assert second.status_code == 409


def test_admin_cannot_delete_self(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    r = client.delete(f"{API}/auth/users/{admin_user_id}", headers=_bearer(token))
    assert r.status_code == 400


def test_admin_can_delete_other_user(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    create = client.post(
        f"{API}/auth/users",
        headers=_bearer(token),
        json={"username": "victim", "password": "pw1234"},
    )
    victim_id = create.json()["data"]["user_id"]
    r = client.delete(f"{API}/auth/users/{victim_id}", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True


def test_admin_cannot_demote_self(client: TestClient, admin_user_id: str):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    r = client.patch(
        f"{API}/auth/users/{admin_user_id}/role",
        headers=_bearer(token),
        json={"role": "user"},
    )
    assert r.status_code == 400


def test_admin_can_change_other_user_password_and_role(
    client: TestClient, admin_user_id: str
):
    token = _login(client, "rootadmin", "adminpw1")["json"]["data"]["token"]
    create = client.post(
        f"{API}/auth/users",
        headers=_bearer(token),
        json={"username": "promoteme", "password": "pw1234"},
    )
    new_user_id = create.json()["data"]["user_id"]

    r1 = client.patch(
        f"{API}/auth/users/{new_user_id}/role",
        headers=_bearer(token),
        json={"role": "admin"},
    )
    assert r1.status_code == 200
    assert r1.json()["data"]["role"] == "admin"

    r2 = client.patch(
        f"{API}/auth/users/{new_user_id}/password",
        headers=_bearer(token),
        json={"password": "newpw1234"},
    )
    assert r2.status_code == 200

    # 老密码失效，新密码可用
    assert _login(client, "promoteme", "pw1234")["status"] == 401
    assert _login(client, "promoteme", "newpw1234")["status"] == 200
