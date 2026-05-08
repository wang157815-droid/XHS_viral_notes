"""Phase 1: XhsCredentialStore + Resolver 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services.xhs_auth import (
    XhsCredential,
    XhsCredentialResolver,
    XhsCredentialStore,
)


@pytest.fixture()
def store(tmp_path: Path) -> XhsCredentialStore:
    return XhsCredentialStore(store_file=str(tmp_path / "xhs_credentials.json"))


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


def test_upsert_creates_then_updates(store: XhsCredentialStore):
    cred = store.upsert(
        redmuse_user_id="u_aaa",
        cookies_path="datas/users/admin/cookies.json",
        xhs_user_id="6411",
        xhs_nickname="管理员小红书号",
        status="unknown",
    )
    assert cred.redmuse_user_id == "u_aaa"
    assert cred.cookies_path == "datas/users/admin/cookies.json"
    assert cred.xhs_user_id == "6411"
    assert cred.status == "unknown"
    assert store.count() == 1

    # 第二次同 redmuse_user_id 是 update 而不是新增
    updated = store.upsert(
        redmuse_user_id="u_aaa",
        cookies_path="datas/users/admin/cookies.json",
        status="active",
        status_message="ok",
    )
    assert updated.status == "active"
    assert updated.status_message == "ok"
    assert store.count() == 1
    assert updated.created_at == cred.created_at  # 不重置 created_at
    assert updated.updated_at >= cred.updated_at


def test_get_by_redmuse_and_xhs_user_id(store: XhsCredentialStore):
    store.upsert(
        redmuse_user_id="u_xyz",
        cookies_path="datas/users/alice/cookies.json",
        xhs_user_id="123abc",
    )
    by_red = store.get_by_redmuse_user_id("u_xyz")
    by_xhs = store.get_by_xhs_user_id("123abc")
    assert by_red is not None and by_red.cookies_path.endswith("alice/cookies.json")
    assert by_xhs is not None and by_xhs.redmuse_user_id == "u_xyz"
    assert store.get_by_redmuse_user_id("u_missing") is None
    assert store.get_by_xhs_user_id("nope") is None


def test_update_status(store: XhsCredentialStore):
    store.upsert(redmuse_user_id="u_a", cookies_path="datas/users/admin/cookies.json")
    updated = store.update_status(
        "u_a",
        status="expired",
        status_message="cookie 过期",
        last_validated_at="2026-05-08T10:00:00+00:00",
    )
    assert updated is not None
    assert updated.status == "expired"
    assert updated.status_message == "cookie 过期"

    # 未知 user_id 返回 None
    assert store.update_status("u_missing", status="active") is None


def test_delete(store: XhsCredentialStore):
    store.upsert(redmuse_user_id="u_a", cookies_path="datas/users/admin/cookies.json")
    assert store.delete("u_a") is True
    assert store.get_by_redmuse_user_id("u_a") is None
    assert store.delete("u_a") is False  # 已经没了


def test_invalid_status_rejected(store: XhsCredentialStore):
    with pytest.raises(ValueError):
        store.upsert(
            redmuse_user_id="u_a",
            cookies_path="datas/users/admin/cookies.json",
            status="totally_made_up",
        )


def test_public_dict_omits_cookies_path(store: XhsCredentialStore):
    cred = store.upsert(
        redmuse_user_id="u_a",
        cookies_path="datas/users/admin/cookies.json",
        xhs_user_id="6411",
    )
    pub = cred.public_dict()
    assert "cookies_path" not in pub
    assert pub["xhs_user_id"] == "6411"
    assert pub["is_bound"] is True


def test_persistence_atomic_write(store: XhsCredentialStore, tmp_path: Path):
    store.upsert(redmuse_user_id="u_a", cookies_path="datas/users/admin/cookies.json")
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert len(raw["credentials"]) == 1


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _write_cookies_file(tmp_path: Path, dirname: str, cookie: str) -> Path:
    folder = tmp_path / "datas" / "users" / dirname
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "cookies.json"
    target.write_text(
        json.dumps(
            {
                "cookie": cookie,
                "created_at": "2026-05-01T00:00:00+00:00",
                "updated_at": "2026-05-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return target


@pytest.fixture()
def isolated_repo_root(tmp_path: Path, monkeypatch):
    """Resolver 内部用 REPO_ROOT 拼绝对路径，把它指到 tmp_path 即可隔离 cookie 文件。"""
    from backend.app.services.xhs_auth import credential_resolver as r_mod

    monkeypatch.setattr(r_mod, "REPO_ROOT", tmp_path, raising=False)
    return tmp_path


def test_resolver_env_override_wins(monkeypatch, store, isolated_repo_root):
    monkeypatch.setenv("XHS_COOKIES_OVERRIDE", "a1=zzz; web_session=qqq")
    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_anyone")
    assert res.found
    assert res.source == "env_override"
    assert res.cookies_str.startswith("a1=zzz")


def test_resolver_credential_path(monkeypatch, store, isolated_repo_root):
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_COOKIE_FALLBACK", raising=False)
    monkeypatch.delenv("COOKIES", raising=False)
    monkeypatch.delenv("COOKIE", raising=False)

    _write_cookies_file(isolated_repo_root, "alice", "a1=alice; web_session=ABC")
    store.upsert(
        redmuse_user_id="u_alice",
        cookies_path="datas/users/alice/cookies.json",
        xhs_user_id="x_a",
    )

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_alice")
    assert res.found
    assert res.source == "credential"
    assert res.cookies_str == "a1=alice; web_session=ABC"
    assert res.cookies_path == "datas/users/alice/cookies.json"
    assert res.redmuse_user_id == "u_alice"
    assert res.xhs_user_id == "x_a"


def test_resolver_missing_credential_no_admin_fallback(
    monkeypatch, store, isolated_repo_root
):
    """没有 credential 记录 + admin fallback 关闭 → 不要悄悄串号。"""
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_COOKIE_FALLBACK", raising=False)
    monkeypatch.delenv("COOKIES", raising=False)
    monkeypatch.delenv("COOKIE", raising=False)

    # admin cookie 文件存在，但因 fallback 关闭不应被使用
    _write_cookies_file(isolated_repo_root, "admin", "a1=ADMIN_LEAK")

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_unbound_user")
    assert not res.found
    assert res.source == "not_found"


def test_resolver_admin_fallback_when_enabled(
    monkeypatch, store, isolated_repo_root
):
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.setenv("ALLOW_ADMIN_COOKIE_FALLBACK", "true")
    monkeypatch.delenv("COOKIES", raising=False)
    monkeypatch.delenv("COOKIE", raising=False)

    _write_cookies_file(isolated_repo_root, "admin", "a1=ADMIN_OK")

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_unbound_user")
    assert res.found
    assert res.source == "admin_fallback"
    assert res.cookies_str == "a1=ADMIN_OK"


def test_resolver_legacy_xhs_user_id_path(monkeypatch, store, isolated_repo_root):
    """旧任务 owner_user_id 是 XHS 数字 ID，identity_store 反查 username。"""
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_COOKIE_FALLBACK", raising=False)

    _write_cookies_file(isolated_repo_root, "xhs_legacy_user", "a1=LEGACY")

    class FakeIdentityStore:
        def get(self, user_id: str):
            if user_id == "5e8c7b":
                return {"username": "xhs_legacy_user"}
            return None

    fake = FakeIdentityStore()
    monkeypatch.setattr(
        "backend.app.services.identity_store.get_identity_store", lambda: fake
    )

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("5e8c7b")
    assert res.found
    assert res.source == "legacy_username"
    assert res.cookies_str == "a1=LEGACY"


def test_resolver_env_compat(monkeypatch, store, isolated_repo_root):
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_COOKIE_FALLBACK", raising=False)
    monkeypatch.setenv("COOKIES", "a1=ENV_COMPAT")

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_no_record")
    assert res.found
    assert res.source == "env_compat"
    assert res.cookies_str == "a1=ENV_COMPAT"


def test_resolver_skips_placeholder_env(monkeypatch, store, isolated_repo_root):
    monkeypatch.delenv("XHS_COOKIES_OVERRIDE", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_COOKIE_FALLBACK", raising=False)
    monkeypatch.setenv("COOKIES", "a1=xxx; web_session=xxx")  # .env.example 占位

    resolver = XhsCredentialResolver(store=store)
    res = resolver.resolve("u_no_record")
    assert not res.found
