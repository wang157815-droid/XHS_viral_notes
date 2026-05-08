"""Phase 0: RedMuseUserStore + bootstrap 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services.redmuse_auth import (
    RedMuseUserStore,
    UserAlreadyExistsError,
    UserNotFoundError,
    bootstrap_admin_if_needed,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# password_hash
# ---------------------------------------------------------------------------


def test_hash_then_verify_roundtrip():
    h = hash_password("hunter2")
    assert h.startswith("$2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_verify_handles_empty_inputs():
    assert verify_password("", "") is False
    assert verify_password("x", "") is False


def test_hash_rejects_empty_password():
    with pytest.raises(ValueError):
        hash_password("")


# ---------------------------------------------------------------------------
# user_store CRUD
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> RedMuseUserStore:
    return RedMuseUserStore(store_file=str(tmp_path / "users.json"))


def test_create_and_get_user(store: RedMuseUserStore):
    user = store.create_user(
        username="Alice",
        password="secret123",
        nickname="爱丽丝",
        role="admin",
        xhs_credential_path="datas/users/admin/cookies.json",
    )
    # username 被规范化为小写
    assert user.username == "alice"
    assert user.nickname == "爱丽丝"
    assert user.role == "admin"
    assert user.user_id.startswith("u_")
    assert user.xhs_credential_path == "datas/users/admin/cookies.json"

    # 通过 user_id / username 都能查回来
    by_id = store.get_by_user_id(user.user_id)
    by_name = store.get_by_username("ALICE")
    assert by_id and by_id.user_id == user.user_id
    assert by_name and by_name.user_id == user.user_id

    # 文件落盘且不含明文密码
    raw = json.loads(Path(store.path).read_text(encoding="utf-8"))
    assert raw["users"][0]["password_hash"].startswith("$2")
    assert "secret123" not in json.dumps(raw)


def test_duplicate_username_raises(store: RedMuseUserStore):
    store.create_user(username="bob", password="pw1234")
    with pytest.raises(UserAlreadyExistsError):
        store.create_user(username="BOB", password="pw5678")


def test_authenticate_success_updates_last_login(store: RedMuseUserStore):
    store.create_user(username="carol", password="pw1234")
    user = store.authenticate("CAROL", "pw1234")
    assert user is not None
    assert user.username == "carol"
    assert user.last_login_at  # 已更新

    # 再次取以确认持久化
    fetched = store.get_by_username("carol")
    assert fetched and fetched.last_login_at == user.last_login_at


def test_authenticate_failure_returns_none(store: RedMuseUserStore):
    store.create_user(username="dave", password="pw1234")
    assert store.authenticate("dave", "wrong") is None
    assert store.authenticate("missing", "anything") is None


def test_disabled_user_cannot_authenticate(store: RedMuseUserStore):
    user = store.create_user(username="ed", password="pw1234")
    store.set_status(user.user_id, "disabled")
    assert store.authenticate("ed", "pw1234") is None


def test_set_password_role_status_and_xhs_path(store: RedMuseUserStore):
    user = store.create_user(username="frank", password="pw1234")
    store.set_password(user.user_id, "newpass99")
    store.set_role(user.user_id, "admin")
    store.set_xhs_credential_path(user.user_id, "datas/users/frank/cookies.json")

    refreshed = store.get_by_user_id(user.user_id)
    assert refreshed
    assert refreshed.role == "admin"
    assert refreshed.xhs_credential_path == "datas/users/frank/cookies.json"
    assert verify_password("newpass99", refreshed.password_hash)


def test_delete_user(store: RedMuseUserStore):
    user = store.create_user(username="grace", password="pw1234")
    assert store.delete_user(user.user_id) is True
    assert store.get_by_user_id(user.user_id) is None
    assert store.delete_user("nonexistent") is False


def test_set_password_on_unknown_user_raises(store: RedMuseUserStore):
    with pytest.raises(UserNotFoundError):
        store.set_password("u_does_not_exist", "anything")


def test_invalid_role_rejected(store: RedMuseUserStore):
    with pytest.raises(ValueError):
        store.create_user(username="x", password="pw1234", role="superuser")


def test_public_dict_excludes_password_hash(store: RedMuseUserStore):
    user = store.create_user(username="harry", password="pw1234")
    public = user.public_dict()
    assert "password_hash" not in public
    assert public["user_id"] == user.user_id
    assert public["username"] == "harry"


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_skips_when_password_missing(
    monkeypatch: pytest.MonkeyPatch, store: RedMuseUserStore
):
    monkeypatch.delenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    result = bootstrap_admin_if_needed(store)
    assert result is None
    assert store.count() == 0


def test_bootstrap_creates_admin_with_xhs_path(
    monkeypatch: pytest.MonkeyPatch, store: RedMuseUserStore
):
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_USER", "rootmuse")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD", "supersecret")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_NICKNAME", "运维管理员")
    monkeypatch.setenv(
        "REDMUSE_BOOTSTRAP_XHS_CREDENTIAL_PATH",
        "datas/users/rootmuse/cookies.json",
    )

    user_id = bootstrap_admin_if_needed(store)
    assert user_id is not None

    user = store.get_by_user_id(user_id)
    assert user is not None
    assert user.username == "rootmuse"
    assert user.nickname == "运维管理员"
    assert user.role == "admin"
    assert user.xhs_credential_path == "datas/users/rootmuse/cookies.json"


def test_bootstrap_noop_when_users_exist(
    monkeypatch: pytest.MonkeyPatch, store: RedMuseUserStore
):
    store.create_user(username="existing_admin", password="pw1234", role="admin")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD", "would-create-admin")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_USER", "newadmin")

    result = bootstrap_admin_if_needed(store)
    assert result is None
    assert store.count() == 1
    assert store.get_by_username("newadmin") is None
