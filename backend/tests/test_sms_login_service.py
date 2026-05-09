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
from typing import Any, List, Optional

import pytest

from backend.app.services.xhs_auth import (
    PhoneReservationStore,
    PhonePurchase,
    SmsCancelledError,
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

    async def click_resend_sms(self) -> None:
        self.calls.append("click_resend_sms")
        self._check("click_resend_sms")

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
        purchases: Optional[List[PhonePurchase]] = None,
        code: str = "123456",
        purchase_error: Optional[Exception] = None,
        sms_error: Optional[Exception] = None,
        sms_delay_seconds: float = 0.0,
        peek_result: Optional[SmsCodeResult] = None,
        peek_error: Optional[Exception] = None,
        wait_outcomes: Optional[List[Any]] = None,
    ) -> None:
        # 单次复用场景仍可传 purchase；多次 acquire 场景传 purchases 列表
        self._purchase = purchase or PhonePurchase(
            order_id="order_xy", phone="85211112222", country_code="HK"
        )
        self._purchases_queue: List[PhonePurchase] = list(purchases or [])
        self._code = code
        self._purchase_error = purchase_error
        self._sms_error = sms_error
        self._sms_delay = sms_delay_seconds
        self._peek_result = peek_result
        self._peek_error = peek_error
        # ``wait_outcomes`` 序列：每次 wait_sms_code 按顺序消费一个元素：
        # - SmsCodeResult / Exception 实例：直接返回 / 抛
        # - None：表示"用默认 SmsCodeResult(_code)"
        # 序列耗尽后回退默认行为（_sms_error or _code）。
        self._wait_outcomes: List[Any] = list(wait_outcomes or [])
        # 监控调用次数：复用 reservation 时不应再调 acquire_phone
        self.acquire_calls: int = 0
        self.wait_calls: List[tuple] = []  # (order_id, seen_codes, timeout)
        self.peek_calls: List[str] = []   # order_id

    async def acquire_phone(self) -> PhonePurchase:
        self.acquire_calls += 1
        if self._purchase_error:
            raise self._purchase_error
        if self._purchases_queue:
            return self._purchases_queue.pop(0)
        return self._purchase

    async def wait_sms_code(self, order_id: str, **kwargs) -> SmsCodeResult:
        seen = list(kwargs.get("seen_codes") or [])
        timeout = kwargs.get("timeout_seconds")
        self.wait_calls.append((order_id, tuple(seen), timeout))
        if self._sms_delay:
            await asyncio.sleep(self._sms_delay)
        # 序列优先
        if self._wait_outcomes:
            outcome = self._wait_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is None:
                return SmsCodeResult(order_id=order_id, code=self._code)
            return outcome
        if self._sms_error:
            raise self._sms_error
        return SmsCodeResult(order_id=order_id, code=self._code)

    async def peek_sms_code(self, order_id: str) -> Optional[SmsCodeResult]:
        self.peek_calls.append(order_id)
        if self._peek_error:
            raise self._peek_error
        return self._peek_result

    async def release_phone(self, order_id: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    provider: FakeSmsProvider,
    driver: FakeDriver,
    *,
    ttl: int = 8,
    store: Optional[PhoneReservationStore] = None,
    resend_interval_sec: int = 1,
    max_sends_per_phone: int = 1,
    max_phones_per_session: int = 1,
    on_login_success: Optional[Any] = None,
) -> SmsLoginService:
    async def factory(_session: SmsLoginSession) -> SmsLoginDriver:
        return driver

    return SmsLoginService(
        provider,
        factory,
        session_ttl_sec=ttl,
        reservation_store=store,
        resend_interval_sec=resend_interval_sec,
        max_sends_per_phone=max_sends_per_phone,
        max_phones_per_session=max_phones_per_session,
        on_login_success=on_login_success,
    )


def _make_store(tmp_path, *, window_sec: int = 1200) -> PhoneReservationStore:
    return PhoneReservationStore(
        store_file=str(tmp_path / "sms_phone_reservations.json"),
        reuse_window_sec=window_sec,
    )


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
async def test_happy_path_success(tmp_path):
    provider = FakeSmsProvider(code="789012")
    driver = FakeDriver(cookies="a1=val; web_session=abc")
    store = _make_store(tmp_path)
    service = _make_service(provider, driver, store=store)

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
async def test_no_stock_error_terminates_with_sms_no_stock(tmp_path):
    provider = FakeSmsProvider(
        purchase_error=SmsNoStockError("暂无库存", code="SMS_NO_STOCK")
    )
    driver = FakeDriver()
    service = _make_service(provider, driver, store=_make_store(tmp_path))

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "SMS_NO_STOCK"

    # 购号失败前应只 open_login_page 过一次；之后必走 cleanup
    assert "open_login_page" in driver.calls
    assert "fill_phone" not in driver.calls
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_sms_timeout_terminates_with_sms_timeout(tmp_path):
    provider = FakeSmsProvider(sms_error=SmsTimeoutError("等待超时", code="SMS_TIMEOUT"))
    driver = FakeDriver()
    service = _make_service(provider, driver, store=_make_store(tmp_path))

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "SMS_TIMEOUT"

    assert "click_send_sms" in driver.calls
    assert "fill_and_submit_sms" not in driver.calls
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_driver_open_failure_terminates_with_driver_error(tmp_path):
    provider = FakeSmsProvider()
    driver = FakeDriver()
    driver.fail_on = "open_login_page"
    service = _make_service(provider, driver, store=_make_store(tmp_path))

    session = await service.create_session("u")
    final = await _wait_for_terminal(service, session.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "DRIVER_ERROR"
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_cancel_session_marks_cancelled_and_calls_cleanup(tmp_path):
    provider = FakeSmsProvider(sms_delay_seconds=5.0)  # 故意拖在 wait_sms_code
    driver = FakeDriver()
    service = _make_service(provider, driver, store=_make_store(tmp_path))

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
async def test_session_auto_expires_when_ttl_passed(tmp_path):
    provider = FakeSmsProvider(sms_delay_seconds=10.0)
    driver = FakeDriver()
    service = _make_service(
        provider, driver, ttl=0, store=_make_store(tmp_path)
    )  # 立刻过期

    session = await service.create_session("u")
    # 直接 get → 因为 ttl=0，第一次 get 就会被标记 EXPIRED
    s = await service.get_session(session.session_id)
    assert s.status == SmsLoginStatus.EXPIRED
    assert s.error_code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown_id(tmp_path):
    provider = FakeSmsProvider()
    driver = FakeDriver()
    service = _make_service(provider, driver, store=_make_store(tmp_path))
    s = await service.get_session("nope")
    assert s is None


@pytest.mark.asyncio
async def test_create_session_rejects_empty_user_id(tmp_path):
    provider = FakeSmsProvider()
    driver = FakeDriver()
    service = _make_service(provider, driver, store=_make_store(tmp_path))
    with pytest.raises(ValueError):
        await service.create_session("")


# ---------------------------------------------------------------------------
# Phase 3 重构补丁：PhoneReservation 复用相关
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_then_retry_reuses_phone_when_sms_not_received(tmp_path):
    """driver 在 fill_phone 失败 → 重试时应复用同一虚拟号，acquire_phone 仅 1 次。"""
    store = _make_store(tmp_path)

    # 第一轮：driver 在 fill_phone 阶段炸 → ERROR
    provider1 = FakeSmsProvider()
    driver1 = FakeDriver()
    driver1.fail_on = "fill_phone"
    service1 = _make_service(provider1, driver1, store=store)
    s1 = await service1.create_session("u_reuse")
    final1 = await _wait_for_terminal(service1, s1.session_id)
    assert final1.status == SmsLoginStatus.ERROR
    assert final1.phone_reused is False
    assert provider1.acquire_calls == 1

    # reservation 应当落盘且 sms_received=False
    saved = store.get("u_reuse")
    assert saved is not None
    assert saved.sms_received is False
    assert saved.order_id == "order_xy"

    # 第二轮：换一个完整可走通的 driver；同 store
    provider2 = FakeSmsProvider(code="999000")
    driver2 = FakeDriver()
    service2 = _make_service(provider2, driver2, store=store)
    s2 = await service2.create_session("u_reuse")
    final2 = await _wait_for_terminal(service2, s2.session_id)
    assert final2.status == SmsLoginStatus.SUCCESS
    # 关键：第二轮应复用，不再调 acquire_phone
    assert provider2.acquire_calls == 0
    assert final2.phone_reused is True
    assert final2.phone == "85211112222"
    assert final2.order_id == "order_xy"
    # 成功后 reservation 必须删除（cookies 已落地）
    assert store.get("u_reuse") is None


@pytest.mark.asyncio
async def test_reservation_not_reused_after_window_expiry(tmp_path):
    """超过 reuse_window_sec 后，下一次会话必须重新购号。"""
    # 1 秒窗口，足够小
    store = _make_store(tmp_path, window_sec=1)

    provider1 = FakeSmsProvider()
    driver1 = FakeDriver()
    driver1.fail_on = "fill_phone"
    service1 = _make_service(provider1, driver1, store=store)
    s1 = await service1.create_session("u_old")
    await _wait_for_terminal(service1, s1.session_id)
    assert store.get("u_old") is not None

    # 等过窗口
    await asyncio.sleep(1.2)

    provider2 = FakeSmsProvider(
        purchase=PhonePurchase(
            order_id="order_new", phone="85299998888", country_code="HK"
        )
    )
    driver2 = FakeDriver()
    service2 = _make_service(provider2, driver2, store=store)
    s2 = await service2.create_session("u_old")
    final2 = await _wait_for_terminal(service2, s2.session_id)
    assert final2.status == SmsLoginStatus.SUCCESS
    assert provider2.acquire_calls == 1  # 重新购号
    assert final2.phone_reused is False
    assert final2.order_id == "order_new"


@pytest.mark.asyncio
async def test_reservation_not_reused_when_sms_already_received(tmp_path):
    """sms_received=True 后即使在 20 分钟内也不复用，避免拿到旧码。"""
    store = _make_store(tmp_path)

    # 第一轮：成功收到验证码后在 fill_and_submit_sms 阶段炸
    provider1 = FakeSmsProvider(code="111222")
    driver1 = FakeDriver()
    driver1.fail_on = "fill_and_submit_sms"
    service1 = _make_service(provider1, driver1, store=store)
    s1 = await service1.create_session("u_burned")
    final1 = await _wait_for_terminal(service1, s1.session_id)
    assert final1.status == SmsLoginStatus.ERROR
    # 关键：sms_received 必须被标记
    saved = store.get("u_burned")
    assert saved is not None
    assert saved.sms_received is True
    assert "111222" in saved.seen_codes

    # 第二轮：必须重新购号
    provider2 = FakeSmsProvider(
        purchase=PhonePurchase(
            order_id="order_fresh", phone="85277776666", country_code="HK"
        )
    )
    driver2 = FakeDriver()
    service2 = _make_service(provider2, driver2, store=store)
    s2 = await service2.create_session("u_burned")
    final2 = await _wait_for_terminal(service2, s2.session_id)
    assert final2.status == SmsLoginStatus.SUCCESS
    assert provider2.acquire_calls == 1
    assert final2.phone_reused is False


@pytest.mark.asyncio
async def test_seen_codes_passed_to_wait_sms_code_on_reuse(tmp_path):
    """复用号时，wait_sms_code 应收到上次已见过的码作为 seen_codes。"""
    store = _make_store(tmp_path)
    # 直接预埋一条已见过码 999111 的 reservation（模拟极端边界：
    # 上次在 fill_and_submit_sms 之前 marker 已经写了 sms_received=True 不可复用，
    # 我们这里手动构造 sms_received=False + seen_codes 不为空 的"准复用"场景）
    from backend.app.services.xhs_auth import PhoneReservation

    store.save(
        PhoneReservation(
            redmuse_user_id="u_seen",
            order_id="order_keep",
            phone="85233334444",
            country_code="HK",
            sms_received=False,
            seen_codes=["999111"],
        )
    )

    provider = FakeSmsProvider(code="888777")
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_seen")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.SUCCESS
    assert final.phone_reused is True
    assert provider.acquire_calls == 0
    # provider.wait_sms_code 必须看到 seen_codes
    assert len(provider.wait_calls) == 1
    assert provider.wait_calls[0][0] == "order_keep"
    assert provider.wait_calls[0][1] == ("999111",)


@pytest.mark.asyncio
async def test_reuse_falls_back_country_code_to_hk_when_missing(tmp_path):
    """复用分支：reservation.country_code 缺失时兜底为 HK，
    保证 driver.fill_phone 能切区号下拉，不会跳过国家码切换。"""
    store = _make_store(tmp_path)
    # 预埋一条 country_code 缺失的 reservation
    from backend.app.services.xhs_auth import PhoneReservation

    store.save(
        PhoneReservation(
            redmuse_user_id="u_no_cc",
            order_id="order_no_cc",
            phone="85291234567",
            country_code="",  # 关键：缺失
            sms_received=False,
        )
    )

    # 第一轮：让 driver 在 fill_phone 之后炸，让 reservation 留下来
    # 这样我们可以验证「兜底为 HK 后被回写持久化」
    provider1 = FakeSmsProvider(code="123123")
    driver1 = FakeDriver()
    driver1.fail_on = "click_send_sms"  # fill_phone 之后炸
    service1 = _make_service(provider1, driver1, store=store)
    s1 = await service1.create_session("u_no_cc")
    final1 = await _wait_for_terminal(service1, s1.session_id)
    assert final1.status == SmsLoginStatus.ERROR
    assert final1.phone_reused is True
    assert provider1.acquire_calls == 0  # 复用了号没新购
    # 关键 1：driver.fill_phone 收到 country_code='HK' 不是空串
    assert driver1.fill_phone_args is not None
    assert driver1.fill_phone_args[0] == "HK"
    # 关键 2：reservation 已经被回写为 HK
    saved = store.get("u_no_cc")
    assert saved is not None
    assert saved.country_code == "HK"


@pytest.mark.asyncio
async def test_new_purchase_falls_back_country_code_to_hk_when_provider_returns_empty(
    tmp_path,
):
    """provider 极少返回 country_code='' 时也兜底，保证 driver 切区号。"""
    store = _make_store(tmp_path)
    # driver 在 fill_phone 之后炸，让 reservation 留下来
    provider = FakeSmsProvider(
        purchase=PhonePurchase(
            order_id="order_blank_cc",
            phone="85299990000",
            country_code="",
        )
    )
    driver = FakeDriver()
    driver.fail_on = "click_send_sms"
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_provider_blank")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.ERROR
    assert driver.fill_phone_args is not None
    assert driver.fill_phone_args[0] == "HK"  # 兜底默认
    saved = store.get("u_provider_blank")
    assert saved is not None
    assert saved.country_code == "HK"


@pytest.mark.asyncio
async def test_reuse_aborts_when_peek_finds_existing_code(tmp_path):
    """**关键加固**：本地 sms_received=False 时也要先 peek hero-sms，
    若 hero-sms 端已有验证码 → 该号已被消费，必须放弃复用、新购。"""
    from backend.app.services.xhs_auth import PhoneReservation

    store = _make_store(tmp_path)
    # 预埋一条「本地以为未消费」的 reservation
    store.save(
        PhoneReservation(
            redmuse_user_id="u_peek_hot",
            order_id="order_already_burned",
            phone="85291110000",
            country_code="HK",
            sms_received=False,  # 关键：本地标记还没写
        )
    )

    # provider 的 peek 返回真实的码 → 表示该号已被消费过
    provider = FakeSmsProvider(
        peek_result=SmsCodeResult(
            order_id="order_already_burned", code="555888"
        ),
        purchase=PhonePurchase(
            order_id="order_fresh", phone="85277770000", country_code="HK"
        ),
    )
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_peek_hot")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.SUCCESS

    # 关键 1：peek 必须被调用过
    assert provider.peek_calls == ["order_already_burned"]
    # 关键 2：发现已有码后转去新购号
    assert provider.acquire_calls == 1
    assert final.phone_reused is False
    assert final.order_id == "order_fresh"
    # 关键 3：旧 reservation 上的 sms_received 必须被持久化为 True，
    # 防止后续会话再次复用它
    # （登录成功后 store 删除了；如果是失败路径才看得到。这里改测中间状态）


@pytest.mark.asyncio
async def test_reuse_aborts_when_peek_returns_cancelled(tmp_path):
    """peek 返回 SmsCancelledError → reservation 删除，新购。"""
    from backend.app.services.xhs_auth import PhoneReservation

    store = _make_store(tmp_path)
    store.save(
        PhoneReservation(
            redmuse_user_id="u_cancelled",
            order_id="order_cancelled",
            phone="85288889999",
            country_code="HK",
            sms_received=False,
        )
    )

    provider = FakeSmsProvider(
        peek_error=SmsCancelledError("订单取消", code="SMS_CANCELLED"),
        purchase=PhonePurchase(
            order_id="order_replacement", phone="85211112222", country_code="HK"
        ),
    )
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_cancelled")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.SUCCESS
    # peek + acquire 各一次
    assert provider.peek_calls == ["order_cancelled"]
    assert provider.acquire_calls == 1
    assert final.phone_reused is False
    assert final.order_id == "order_replacement"


@pytest.mark.asyncio
async def test_reuse_proceeds_when_peek_returns_none(tmp_path):
    """peek 返回 None（hero-sms 端确认没收过码）→ 真复用，acquire_phone 不调用。"""
    from backend.app.services.xhs_auth import PhoneReservation

    store = _make_store(tmp_path)
    store.save(
        PhoneReservation(
            redmuse_user_id="u_clean",
            order_id="order_clean",
            phone="85266667777",
            country_code="HK",
            sms_received=False,
        )
    )

    provider = FakeSmsProvider(peek_result=None, code="200300")
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_clean")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.SUCCESS
    # 关键：peek 调用过，acquire 没调用
    assert provider.peek_calls == ["order_clean"]
    assert provider.acquire_calls == 0
    assert final.phone_reused is True
    assert final.order_id == "order_clean"


@pytest.mark.asyncio
async def test_reuse_aborts_when_peek_raises_transport_error(tmp_path):
    """peek 因网络抖动抛 SmsTransportError → 保守起见放弃复用，新购。"""
    from backend.app.services.xhs_auth import PhoneReservation
    from backend.app.services.xhs_auth import SmsTransportError

    store = _make_store(tmp_path)
    store.save(
        PhoneReservation(
            redmuse_user_id="u_flaky",
            order_id="order_flaky",
            phone="85299995555",
            country_code="HK",
            sms_received=False,
        )
    )

    provider = FakeSmsProvider(
        peek_error=SmsTransportError("network", code="SMS_TRANSPORT_ERROR"),
        purchase=PhonePurchase(
            order_id="order_safe", phone="85222223333", country_code="HK"
        ),
    )
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_flaky")
    final = await _wait_for_terminal(service, s.session_id)
    assert final.status == SmsLoginStatus.SUCCESS
    assert provider.peek_calls == ["order_flaky"]
    assert provider.acquire_calls == 1  # 网络抖动 → 保守新购
    assert final.phone_reused is False


@pytest.mark.asyncio
async def test_reservation_kept_on_user_cancel(tmp_path):
    """用户主动取消时 reservation 不删，下次还能复用。"""
    store = _make_store(tmp_path)

    provider = FakeSmsProvider(sms_delay_seconds=5.0)
    driver = FakeDriver()
    service = _make_service(provider, driver, store=store)
    s = await service.create_session("u_cancel")

    # 等到 reservation 已写入（acquiring_phone 之后）
    for _ in range(60):
        if store.get("u_cancel") is not None:
            break
        await asyncio.sleep(0.02)
    assert store.get("u_cancel") is not None

    await service.cancel_session(s.session_id)
    s_after = await service.get_session(s.session_id)
    assert s_after.status == SmsLoginStatus.CANCELLED

    # cancel 后 reservation 仍在
    saved = store.get("u_cancel")
    assert saved is not None
    assert saved.sms_received is False  # 没收到验证码 → 仍可复用


# ---------------------------------------------------------------------------
# 重发 + 换号循环（用户实测需求：3 分钟间隔、N 次重发、M 次换号）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_after_timeout_then_succeed(tmp_path):
    """同一个号：第一次 wait_sms 超时 → driver.click_resend_sms → 第二次收到验证码。

    断言：
    - SUCCESS 终态、cookies 拿到
    - driver.calls 恰好 click_send_sms 一次 + click_resend_sms 一次
    - provider.wait_calls 两次，timeout=resend_interval_sec
    - acquire_phone 只调一次（同一个号）
    """
    timeout_err = SmsTimeoutError("等待超时", code="SMS_TIMEOUT")
    provider = FakeSmsProvider(
        wait_outcomes=[
            timeout_err,  # 第 1 次 180s 没等到
            SmsCodeResult(order_id="order_xy", code="246810"),  # 第 2 次收到
        ]
    )
    driver = FakeDriver()
    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        resend_interval_sec=1,
        max_sends_per_phone=2,
        max_phones_per_session=1,
    )

    session = await service.create_session("u_resend")
    final = await _wait_for_terminal(service, session.session_id, timeout=8.0)

    assert final.status == SmsLoginStatus.SUCCESS
    assert final.cookies_str  # cookies 已写入
    # 第一次发送 + 一次重发
    assert driver.calls.count("click_send_sms") == 1
    assert driver.calls.count("click_resend_sms") == 1
    assert driver.fill_sms_arg == "246810"
    # provider 被轮询了 2 次
    assert len(provider.wait_calls) == 2
    assert all(call[2] == 1 for call in provider.wait_calls)  # timeout=1
    # 同号：acquire 只一次
    assert provider.acquire_calls == 1


@pytest.mark.asyncio
async def test_switch_phone_after_all_resends_timeout(tmp_path):
    """同号 N 次发送都超时 → 删 reservation → 换号 → 第二个号成功。"""
    timeout_err = SmsTimeoutError("等待超时", code="SMS_TIMEOUT")
    provider = FakeSmsProvider(
        purchases=[
            PhonePurchase(order_id="order_A", phone="85211110001", country_code="HK"),
            PhonePurchase(order_id="order_B", phone="85211110002", country_code="HK"),
        ],
        wait_outcomes=[
            timeout_err,  # phone A: send 1 超时
            timeout_err,  # phone A: send 2 超时
            SmsCodeResult(order_id="order_B", code="135790"),  # phone B: send 1 收到
        ],
    )
    driver = FakeDriver()
    store = _make_store(tmp_path)
    service = _make_service(
        provider,
        driver,
        store=store,
        resend_interval_sec=1,
        max_sends_per_phone=2,
        max_phones_per_session=2,
    )

    session = await service.create_session("u_switch")
    final = await _wait_for_terminal(service, session.session_id, timeout=10.0)

    assert final.status == SmsLoginStatus.SUCCESS
    # 两个号都被购了
    assert provider.acquire_calls == 2
    # 顺序：fill A → send → resend → fill B → send（不再 resend，第一次就成）
    fill_phone_indices = [i for i, c in enumerate(driver.calls) if c == "fill_phone"]
    assert len(fill_phone_indices) == 2  # A 和 B 各填一次
    # 总共 send + resend 调用次数 = 2(A) + 1(B) = 3
    total_sends = driver.calls.count("click_send_sms") + driver.calls.count(
        "click_resend_sms"
    )
    assert total_sends == 3
    # 最终 cookies 是 phone B 的码
    assert driver.fill_sms_arg == "135790"
    # 成功后 reservation 被删
    assert store.get("u_switch") is None


@pytest.mark.asyncio
async def test_all_phones_exhausted_terminates_with_sms_timeout(tmp_path):
    """3 个号 × 2 次发送都超时 → ERROR + SMS_TIMEOUT。"""
    timeout_err = SmsTimeoutError("等待超时", code="SMS_TIMEOUT")
    provider = FakeSmsProvider(
        purchases=[
            PhonePurchase(order_id=f"order_{i}", phone=f"8521111000{i}", country_code="HK")
            for i in range(3)
        ],
        wait_outcomes=[timeout_err] * 6,  # 3 个号 × 2 次发送 = 6 次超时
    )
    driver = FakeDriver()
    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        resend_interval_sec=1,
        max_sends_per_phone=2,
        max_phones_per_session=3,
    )

    session = await service.create_session("u_exhaust")
    final = await _wait_for_terminal(service, session.session_id, timeout=15.0)

    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "SMS_TIMEOUT"
    # 真的购了 3 个号
    assert provider.acquire_calls == 3
    # 真的发了 6 次（3 × 2）
    total_sends = driver.calls.count("click_send_sms") + driver.calls.count(
        "click_resend_sms"
    )
    assert total_sends == 6
    # 没有进入 fill_and_submit
    assert "fill_and_submit_sms" not in driver.calls
    assert driver.cleanup_called


@pytest.mark.asyncio
async def test_default_session_ttl_derived_from_retry_params():
    """SmsLoginService 不传 session_ttl_sec 时，默认 = M*N*interval+120。"""
    provider = FakeSmsProvider()
    driver = FakeDriver()

    async def factory(_session: SmsLoginSession) -> SmsLoginDriver:
        return driver

    service = SmsLoginService(
        provider,
        factory,
        resend_interval_sec=180,
        max_sends_per_phone=2,
        max_phones_per_session=3,
    )
    # 3 * 2 * 180 + 120 = 1200
    assert service._session_ttl_sec == 1200


@pytest.mark.asyncio
async def test_session_dto_exposes_phone_attempt_and_send_attempt(tmp_path):
    """前端能看到「正在用第几个号、第几次发送」。"""
    timeout_err = SmsTimeoutError("等待超时", code="SMS_TIMEOUT")
    provider = FakeSmsProvider(
        wait_outcomes=[
            timeout_err,  # send 1 超时
            SmsCodeResult(order_id="order_xy", code="246810"),  # send 2 收到
        ]
    )
    driver = FakeDriver()
    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        resend_interval_sec=1,
        max_sends_per_phone=2,
        max_phones_per_session=1,
    )

    session = await service.create_session("u_dto")
    final = await _wait_for_terminal(service, session.session_id, timeout=8.0)

    assert final.status == SmsLoginStatus.SUCCESS
    dto = final.public_dict()
    # 收到验证码时刚好在 send_attempt=2 的 phone_attempt=1
    assert dto["phone_attempt"] == 1
    assert dto["send_attempt"] == 2


# ---------------------------------------------------------------------------
# on_login_success hook：service 拿到 cookies 后自动绑定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_bind_hook_success_marks_session_and_clears_cookies(tmp_path):
    """挂了 hook 且 hook 返回 dict → SUCCESS + bind_result 写入 + cookies_str 清空。"""
    provider = FakeSmsProvider(code="246810")
    driver = FakeDriver(cookies="a1=val; web_session=long_enough_session_value")
    captured: list[tuple[str, str]] = []

    async def hook(session: SmsLoginSession, cookies_str: str):
        captured.append((session.redmuse_user_id, cookies_str))
        return {
            "success": True,
            "redmuse_user_id": session.redmuse_user_id,
            "xhs_user_id": "5f8a1b2c3d",
            "xhs_nickname": "测试昵称",
            "credential": {"cookies_path": "datas/users/test/cookies.json"},
        }

    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        on_login_success=hook,
    )
    session = await service.create_session("redmuse_u1")
    final = await _wait_for_terminal(service, session.session_id, timeout=5.0)

    assert final.status == SmsLoginStatus.SUCCESS
    assert final.bind_result is not None
    assert final.bind_result["xhs_user_id"] == "5f8a1b2c3d"
    assert final.bind_result["xhs_nickname"] == "测试昵称"

    # cookies 已被 hook 消费 + service 清空，二次 consume 拿不到
    assert final.cookies_str is None
    assert await service.consume_cookies(session.session_id) is None

    # public_dict 暴露 auto_bound + bind_result
    dto = final.public_dict()
    assert dto["auto_bound"] is True
    assert dto["bind_result"]["xhs_nickname"] == "测试昵称"
    assert dto["cookies_ready"] is False

    # hook 收到了正确参数
    assert captured == [("redmuse_u1", "a1=val; web_session=long_enough_session_value")]


@pytest.mark.asyncio
async def test_auto_bind_hook_returns_none_marks_error(tmp_path):
    """hook 返回 None（如 selfinfo 失败 / guest 身份）→ ERROR + BIND_FAILED。"""
    provider = FakeSmsProvider(code="135790")
    driver = FakeDriver(cookies="a1=val; web_session=guest_xx")

    async def hook(_session: SmsLoginSession, _cookies: str):
        return None  # 模拟 binder.bind_with_cookies result.success=False

    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        on_login_success=hook,
    )
    session = await service.create_session("redmuse_u2")
    final = await _wait_for_terminal(service, session.session_id, timeout=5.0)

    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "BIND_FAILED"
    assert "selfinfo" in (final.error_message or "")
    assert final.bind_result is None
    # cookies 不再可消费（终态非 SUCCESS）
    assert await service.consume_cookies(session.session_id) is None


@pytest.mark.asyncio
async def test_auto_bind_hook_raises_marks_error(tmp_path):
    """hook 抛异常 → ERROR + BIND_FAILED；不向上冒泡阻塞 service。"""
    provider = FakeSmsProvider(code="000111")
    driver = FakeDriver(cookies="a1=val; web_session=ok")

    async def hook(_session: SmsLoginSession, _cookies: str):
        raise RuntimeError("binder unreachable")

    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        on_login_success=hook,
    )
    session = await service.create_session("redmuse_u3")
    final = await _wait_for_terminal(service, session.session_id, timeout=5.0)

    assert final.status == SmsLoginStatus.ERROR
    assert final.error_code == "BIND_FAILED"
    assert "binder unreachable" in (final.error_message or "")


@pytest.mark.asyncio
async def test_no_hook_keeps_legacy_cookies_consumable(tmp_path):
    """未注入 hook（旧行为）→ SUCCESS 后 cookies 仍可被 consume_cookies 取走一次。"""
    provider = FakeSmsProvider(code="999000")
    driver = FakeDriver(cookies="a1=val; web_session=legacy_ok")

    service = _make_service(
        provider,
        driver,
        store=_make_store(tmp_path),
        on_login_success=None,  # 显式不挂 hook
    )
    session = await service.create_session("redmuse_legacy")
    final = await _wait_for_terminal(service, session.session_id, timeout=5.0)

    assert final.status == SmsLoginStatus.SUCCESS
    assert final.bind_result is None
    assert final.public_dict()["auto_bound"] is False

    # 老接口语义：cookies 仍然可 consume 一次
    cookies = await service.consume_cookies(session.session_id)
    assert cookies == "a1=val; web_session=legacy_ok"
    # 二次 consume 为 None
    assert await service.consume_cookies(session.session_id) is None
