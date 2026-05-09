"""Phase 3-B: SmsLoginService 状态机单测（不真启 Playwright）。

注入：
- ``FakeSmsProvider``：可控制 acquire_phone / wait_sms_code 的成功/异常 / 延时
- ``FakeDriver``：记录方法调用顺序，配合 driver_factory 注入

覆盖：
- 完整 happy path：状态依次切到 success
- 购号无库存 → ERROR + SMS_NO_STOCK
- 验证码超时 → ERROR + SMS_TIMEOUT
- 用户主动 cancel：状态置为 CANCELLED，driver.cleanup 触发
- consume_cookies：成功后 cookies 仅可被消费一次
- 自动过期：超过 TTL 后 get_session 把状态置为 EXPIRED
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest

from backend.app.services.xhs_auth import (
    PhonePurchase,
    SmsCodeResult,
    SmsNoStockError,
    SmsTimeoutError,
)
from backend.app.services.xhs_auth.sms_login_driver import CookieSnapshot, SmsLoginDriver
from backend.app.services.xhs_auth.sms_login_service import (
    SmsLoginService,
    SmsLoginSession,
    SmsLoginStatus,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDriver(SmsLoginDriver):
    def __init__(self, *, cookies: str = "a1=x; web_session=ok") -> None:
        self.calls: List[str] = []
        self._cookies = cookies
        self.fail_on: Optional[str] = None
        self.cleanup_called = False
        self.fill_phone_args: Optional[tuple] = None
        self.fill_sms_arg: Optional[str] = None

    def _check(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"fake driver fail at {name}")

    async def open_login_page(self) -> None:
        self.calls.append("open_login_page")
        self._check("open_login_page")

    async def fill_phone(self, *, country_code: str, phone: str) -> None:
        self.calls.append("fill_phone")
        self.fill_phone_args = (country_code, phone)
        self._check("fill_phone")

    async def click_send_sms(self) -> None:
        self.calls.append("click_send_sms")
        self._check("click_send_sms")

    async def fill_and_submit_sms(self, code: str) -> None:
        self.calls.append("fill_and_submit_sms")
        self.fill_sms_arg = code
        self._check("fill_and_submit_sms")

    async def wait_for_login_success(self, *, timeout_seconds: int = 60) -> CookieSnapshot:
        self.calls.append("wait_for_login_success")
        self._check("wait_for_login_success")
        return CookieSnapshot(cookies_str=self._cookies, raw_count=2)

    async def cleanup(self) -> None:
        self.calls.append("cleanup")
        self.cleanup_called = True


class FakeSmsProvider:
    """实现 SmsProvider 鸭子接口（acquire_phone / wait_sms_code）。"""

    name = "fake_sms"

    def __init__(
        self,
        *,
        purchase: Optional[PhonePurchase] = None,
        code: str = "123456",
        purchase_error: Optional[Exception] = None,
        sms_error: Optional[Exception] = None,
        sms_delay_seconds: float = 0.0,
    ) -> None:
        self._purchase = purchase or PhonePurchase(
            order_id="order_xy", phone="85211112222", country_code="HK"
        )
        self._code = code
        self._purchase_error = purchase_error
        self._sms_error = sms_error
        self._sms_delay = sms_delay_seconds

    async def acquire_phone(self) -> PhonePurchase:
        if self._purchase_error:
            raise self._purchase_error
        return self._purchase

    async def wait_sms_code(self, order_id: str, **kwargs) -> SmsCodeResult:
        if self._sms_delay:
            await asyncio.sleep(self._sms_delay)
        if self._sms_error:
            raise self._sms_error
        return SmsCodeResult(order_id=order_id, code=self._code)

    async def release_phone(self, order_id: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(provider: FakeSmsProvider, driver: FakeDriver, *, ttl: int = 8) -> SmsLoginService:
    async def factory(_session: SmsLoginSession) -> SmsLoginDriver:
        return driver

    return SmsLoginService(provider, factory, session_ttl_sec=ttl)


async def _wait_for_terminal(
    service: SmsLoginService, session_id: str, *, timeout: float = 5.0
) -> SmsLoginSession:
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        s = await service.get_session(session_id)
        if s and s.status.is_terminal:
            return s
        await asyncio.sleep(0.02)
    raise AssertionError(f"等待 session {session_id} 终止超时")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_success():
    provider = FakeSmsProvider(code="789012")
    driver = FakeDriver(cookies="a1=val; web_session=abc")
    service = _make_service(provider, driver)

    session = await service.create_session("redmuse_user_1")
    assert session.status == SmsLoginStatus.INITIALIZING

    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.SUCCESS
    assert final.error_code is None
    assert final.phone == "85211112222"
    assert final.phone_country == "HK"
    assert final.order_id == "order_xy"

    # driver 调用顺序
    assert driver.calls == [
        "open_login_page",
        "fill_phone",
        "click_send_sms",
        "fill_and_submit_sms",
        "wait_for_login_success",
        "cleanup",
    ]
    assert driver.fill_phone_args == ("HK", "85211112222")
    assert driver.fill_sms_arg == "789012"

    # public_dict 应做 phone mask
    pub = final.public_dict()
    assert pub["status"] == "success"
    assert "***" in pub["phone"]
    assert pub["cookies_ready"] is True

    # consume_cookies 仅一次
    cookies1 = await service.consume_cookies(session.session_id)
    assert cookies1 == "a1=val; web_session=abc"
    cookies2 = await service.consume_cookies(session.session_id)
    assert cookies2 is None


@pytest.mark.asyncio
async def test_no_stock_error_terminates_with_sms_no_stock():
    provider = FakeSmsProvider(
        purchase_error=SmsNoStockError("暂无库存", code="SMS_NO_STOCK")
    )
    driver = FakeDriver()
    service = _make_service(provider, driver)

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "SMS_NO_STOCK"

    # 购号失败前应只 open_login_page 过一次；之后必走 cleanup
    assert "open_login_page" in driver.calls
    assert "fill_phone" not in driver.calls
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_sms_timeout_terminates_with_sms_timeout():
    provider = FakeSmsProvider(sms_error=SmsTimeoutError("等待超时", code="SMS_TIMEOUT"))
    driver = FakeDriver()
    service = _make_service(provider, driver)

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "SMS_TIMEOUT"

    assert "click_send_sms" in driver.calls
    assert "fill_and_submit_sms" not in driver.calls
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_driver_open_failure_terminates_with_driver_error():
    provider = FakeSmsProvider()
    driver = FakeDriver()
    driver.fail_on = "open_login_page"
    service = _make_service(provider, driver)

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "DRIVER_ERROR"
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_cancel_session_marks_cancelled_and_calls_cleanup():
    provider = FakeSmsProvider(sms_delay_seconds=5.0)  # 故意拖在 wait_sms_code
    driver = FakeDriver()
    service = _make_service(provider, driver)

    session = await service.create_session("u")
    # 等到 driver 至少进了 click_send_sms 阶段
    for _ in range(50):
        s = await service.get_session(session.session_id)
        if s and s.status == SmsLoginStatus.WAITING_SMS:
            break
        await asyncio.sleep(0.02)

    assert await service.cancel_session(session.session_id) is True

    s_after = await service.get_session(session.session_id)
    assert s_after.status == SmsLoginStatus.CANCELLED
    assert s_after.error_code == "SESSION_CANCELLED"

    # cleanup 在 _run_session finally / cancel 路径都会触发；最终一定调用了
    # 给后台 task 一点时间结束
    for _ in range(50):
        if driver.cleanup_called:
            break
        await asyncio.sleep(0.02)
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_session_auto_expires_when_ttl_passed():
    provider = FakeSmsProvider(sms_delay_seconds=10.0)
    driver = FakeDriver()
    service = _make_service(provider, driver, ttl=0)  # 立刻过期

    session = await service.create_session("u")
    # 直接 get → 因为 ttl=0，第一次 get 就会被标记 EXPIRED
    s = await service.get_session(session.session_id)
    assert s.status == SmsLoginStatus.EXPIRED
    assert s.error_code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown_id():
    provider = FakeSmsProvider()
    driver = FakeDriver()
    service = _make_service(provider, driver)
    s = await service.get_session("nope")
    assert s is None


@pytest.mark.asyncio
async def test_create_session_rejects_empty_user_id():
    provider = FakeSmsProvider()
    driver = FakeDriver()
    service = _make_service(provider, driver)
    with pytest.raises(ValueError):
        await service.create_session("")
