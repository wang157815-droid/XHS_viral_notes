"""Phase 1: bootstrap_admin_if_needed 是否同步写入 XhsCredentialStore。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.redmuse_auth import (
    bootstrap_admin_if_needed,
    get_user_store,
)
from backend.app.services.xhs_auth import get_credential_store


def test_bootstrap_seeds_xhs_credential(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_USER", "rootmuse")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD", "supersecret")
    monkeypatch.setenv(
        "REDMUSE_BOOTSTRAP_XHS_CREDENTIAL_PATH",
        "datas/users/admin/cookies.json",
    )

    user_id = bootstrap_admin_if_needed()
    assert user_id is not None

    cred = get_credential_store().get_by_redmuse_user_id(user_id)
    assert cred is not None
    assert cred.cookies_path == "datas/users/admin/cookies.json"
    assert cred.status == "unknown"  # bootstrap 时尚未健康检查
    assert cred.status_message  # 描述性文本，方便排查


def test_bootstrap_uses_default_xhs_path_when_env_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    """空环境变量回退默认路径 ``datas/users/admin/cookies.json``，
    XhsCredentialStore 也用同一个默认值。"""
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_USER", "headless")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_ADMIN_PASSWORD", "supersecret")
    monkeypatch.setenv("REDMUSE_BOOTSTRAP_XHS_CREDENTIAL_PATH", "")

    user_id = bootstrap_admin_if_needed()
    assert user_id is not None

    cred = get_credential_store().get_by_redmuse_user_id(user_id)
    assert cred is not None
    assert cred.cookies_path == "datas/users/admin/cookies.json"

    user = get_user_store().get_by_user_id(user_id)
    assert user is not None
    assert user.xhs_credential_path == "datas/users/admin/cookies.json"
