"""Phase 3: /xhs-auth/sms-login/* 路由集成测试。

- 用 FakeDriver + FakeSmsProvider 构造隔离 service，通过
  ``set_default_service`` 注入路由使用的单例；
- 覆盖 create / get / cancel / bind 四个接口 + 服务未配置、归属校验、
  SUCCESS 前 bind 返回 409、cookies 被消费一次。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from backend.app.services.xhs_auth import (
    PhonePurchase,
    SmsCodeResult,
)
from backend.app.services.xhs_auth.sms_login_driver import CookieSnapshot, SmsLoginDriver
from backend.app.services.xhs_auth.sms_login_service import (
    SmsLoginService,
    SmsLoginStatus,
    set_default_service,
)


API = "/api/v1"


# ---------------------------------------------------------------------------
# Fakes（与 test_sms_login_service 的思路一致）
# ---------------------------------------------------------------------------


class FakeDriver(SmsLoginDriver):
    def __init__(self, cookies: str = "a1=qrcookie; web_session=ok") -> None:
        self._cookies = cookies
        self.cleanup_called = False

    async def open_login_page(self) -> None:
        pass

    async def fill_phone(self, *, country_code: str, phone: str) -> None:
        pass

    async def click_send_sms(self) -> None:
        pass

    async def fill_and_submit_sms(self, code: str) -> None:
        pass

    async def wait_for_login_success(self, *, timeout_seconds: int = 60) -> CookieSnapshot:
        return CookieSnapshot(cookies_str=self._cookies, raw_count=2)

    async def cleanup(self) -> None:
        self.cleanup_called = True


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._purchase = PhonePurchase(
            order_id="order_1", phone="85211112222", country_code="HK"
        )
        self._code = "998877"

    async def acquire_phone(self) -> PhonePurchase:
        return self._purchase

    async def wait_sms_code(self, order_id: str, **_: Any) -> SmsCodeResult:
        return SmsCodeResult(order_id=order_id, code=self._code)

    async def release_phone(self, order_id: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _bearer(t: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture()
def client_auth(monkeypatch):
    """登录 admin 拿 token + 把 FakeSmsProvider 注入到 SmsLoginService 单例。"""
    from backend.app.main import app
    from backend.app.services.redmuse_auth import get_user_store
    from backend.app.services.xhs_auth import credential_binder as binder_mod

    app.dependency_overrides.clear()
    client = TestClient(app)

    store = get_user_store()
    store.create_user(username="phase3admin", password="adminpw1", role="admin")
    login = client.post(
        f"{API}/auth/login",
        json={"username": "phase3admin", "password": "adminpw1"},
    )
    body = login.json()["data"]
    token = body["token"]
    user_id = body["user"]["user_id"]

    # 注入 sms login service（fake provider + fake driver）
    driver = FakeDriver()

    async def driver_factory(_session):
        return driver

    provider = FakeProvider()
    svc = SmsLoginService(provider, driver_factory, session_ttl_sec=60)
    set_default_service(svc)

    # 注入 binder 的 fake orchestrator（绕开 selfinfo 真实调用）
    class _FakeOrch:
        def extract_xhs_identity_from_cookies(self, cookies_str: str):
            return {"user_id": "xhs_abc", "nickname": "SMS测试号", "username": "xhs_xhs_abc"}

        async def get_session_cookies_str(self, session_id: str):
            return None

    monkeypatch.setattr(
        binder_mod,
        "_default_binder",
        binder_mod.XhsCredentialBinder(orchestrator=_FakeOrch()),
        raising=False,
    )

    yield {
        "client": client,
        "token": token,
        "user_id": user_id,
        "driver": driver,
        "provider": provider,
        "svc": svc,
    }

    # cleanup
    set_default_service(None)


def _wait_status(
    client: TestClient, token: str, session_id: str, target: str, *, timeout: float = 5.0
) -> Dict[str, Any]:
    import time

    end = time.time() + timeout
    while time.time() < end:
        r = client.get(
            f"{API}/xhs-auth/sms-login/session/{session_id}",
            headers=_bearer(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        if body["status"] == target:
            return body
        time.sleep(0.05)
    raise AssertionError(f"未在 {timeout}s 内到达状态 {target}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_session_requires_auth(client_auth):
    r = client_auth["client"].post(f"{API}/xhs-auth/sms-login/session")
    assert r.status_code == 401


def test_create_then_bind_full_flow(client_auth):
    ctx = client_auth
    c = ctx["client"]

    r = c.post(f"{API}/xhs-auth/sms-login/session", headers=_bearer(ctx["token"]))
    assert r.status_code == 200, r.text
    session = r.json()["data"]
    assert session["status"] in ("initializing", "acquiring_phone", "sending_sms")
    assert session["redmuse_user_id"] == ctx["user_id"]

    # 等待 success
    succ = _wait_status(c, ctx["token"], session["session_id"], "success")
    assert succ["cookies_ready"] is True
    assert succ["phone"].startswith("85") and "***" in succ["phone"]

    # bind
    r2 = c.post(
        f"{API}/xhs-auth/sms-login/session/{session['session_id']}/bind",
        headers=_bearer(ctx["token"]),
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()["data"]
    assert body["success"] is True
    assert body["xhs_user_id"] == "xhs_abc"

    # 再次 bind 应 410（cookies 已被消费）
    r3 = c.post(
        f"{API}/xhs-auth/sms-login/session/{session['session_id']}/bind",
        headers=_bearer(ctx["token"]),
    )
    assert r3.status_code == 410
    assert r3.json()["error"]["code"] == "SMS_COOKIES_CONSUMED"


def test_bind_before_success_returns_409(client_auth, monkeypatch):
    """把 provider 换成永远阻塞 → 状态停在 waiting_sms，bind 应 409。"""
    from backend.app.services.xhs_auth import sms_login_service as svc_mod

    ctx = client_auth
    c = ctx["client"]

    class _SlowProvider(FakeProvider):
        async def wait_sms_code(self, order_id: str, **_: Any) -> SmsCodeResult:
            await asyncio.sleep(10)
            return SmsCodeResult(order_id=order_id, code="0")

    driver = FakeDriver()

    async def factory(_s):
        return driver

    slow_svc = SmsLoginService(_SlowProvider(), factory, session_ttl_sec=60)
    set_default_service(slow_svc)

    r = c.post(f"{API}/xhs-auth/sms-login/session", headers=_bearer(ctx["token"]))
    session_id = r.json()["data"]["session_id"]
    _wait_status(c, ctx["token"], session_id, "waiting_sms")

    r2 = c.post(
        f"{API}/xhs-auth/sms-login/session/{session_id}/bind",
        headers=_bearer(ctx["token"]),
    )
    assert r2.status_code == 409
    err = r2.json()["error"]
    assert err["code"] == "SMS_SESSION_NOT_READY"


def test_get_and_cancel_session(client_auth):
    ctx = client_auth
    c = ctx["client"]

    r = c.post(f"{API}/xhs-auth/sms-login/session", headers=_bearer(ctx["token"]))
    sid = r.json()["data"]["session_id"]

    # GET
    r2 = c.get(
        f"{API}/xhs-auth/sms-login/session/{sid}", headers=_bearer(ctx["token"])
    )
    assert r2.status_code == 200

    # 404
    r3 = c.get(
        f"{API}/xhs-auth/sms-login/session/nope", headers=_bearer(ctx["token"])
    )
    assert r3.status_code == 404

    # DELETE / cancel
    r4 = c.delete(
        f"{API}/xhs-auth/sms-login/session/{sid}", headers=_bearer(ctx["token"])
    )
    assert r4.status_code == 200
    assert r4.json()["data"]["cancelled"] is True


def test_session_ownership_enforced(client_auth):
    """另一个 RedMuse 用户登录后不能查/取消别人会话。"""
    ctx = client_auth
    c = ctx["client"]

    # 启动 owner 会话
    r = c.post(f"{API}/xhs-auth/sms-login/session", headers=_bearer(ctx["token"]))
    sid = r.json()["data"]["session_id"]

    # 创建第二个 user
    from backend.app.services.redmuse_auth import get_user_store

    get_user_store().create_user(
        username="stranger_phase3", password="strangerpw", role="user"
    )
    login2 = c.post(
        f"{API}/auth/login",
        json={"username": "stranger_phase3", "password": "strangerpw"},
    )
    token2 = login2.json()["data"]["token"]

    r_get = c.get(
        f"{API}/xhs-auth/sms-login/session/{sid}", headers=_bearer(token2)
    )
    assert r_get.status_code == 403

    r_del = c.delete(
        f"{API}/xhs-auth/sms-login/session/{sid}", headers=_bearer(token2)
    )
    assert r_del.status_code == 403


def test_service_not_configured_returns_503(monkeypatch):
    """未配置 SMS_PROVIDER_API_KEY / 未调用 set_default_service → 503。"""
    from backend.app.main import app
    from backend.app.services.redmuse_auth import get_user_store

    app.dependency_overrides.clear()
    client = TestClient(app)

    get_user_store().create_user(
        username="noprovider_admin", password="adminpw1", role="admin"
    )
    login = client.post(
        f"{API}/auth/login",
        json={"username": "noprovider_admin", "password": "adminpw1"},
    )
    token = login.json()["data"]["token"]

    # 关闭默认 service，并清空 api_key
    set_default_service(None)
    monkeypatch.setenv("SMS_PROVIDER_API_KEY", "")

    r = client.post(f"{API}/xhs-auth/sms-login/session", headers=_bearer(token))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SMS_PROVIDER_NOT_CONFIGURED"
