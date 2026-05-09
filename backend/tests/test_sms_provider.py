"""Phase 3-A: HeroSmsProvider 单元测试。

不打 hero-sms 真实 API：注入一个 mock httpx.AsyncClient，按 GET 请求的
``params["action"]`` 返回预设响应序列。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional

import httpx
import pytest

from backend.app.services.xhs_auth import (
    HeroSmsProvider,
    PhonePurchase,
    SmsAuthError,
    SmsCancelledError,
    SmsCodeResult,
    SmsNoStockError,
    SmsResponseError,
    SmsTimeoutError,
)


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _Recorder:
    """记录每次 GET 请求的参数；按 action 派发预设响应队列。"""

    def __init__(self) -> None:
        # action → deque[_FakeResponse]
        self.responses: Dict[str, Deque[_FakeResponse]] = {}
        self.calls: List[Dict[str, str]] = []

    def queue(self, action: str, *responses: _FakeResponse) -> None:
        bucket = self.responses.setdefault(action, deque())
        for r in responses:
            bucket.append(r)

    async def get(
        self,
        url: str,
        *,
        params: Dict[str, Any],
        timeout: Optional[int] = None,
    ) -> _FakeResponse:
        self.calls.append(dict(params))
        action = str(params.get("action") or "")
        bucket = self.responses.get(action)
        if not bucket:
            raise AssertionError(f"测试未为 action={action} 排队响应")
        return bucket.popleft()


@pytest.fixture()
def fake_client():
    """构造 httpx.AsyncClient 接口兼容的 stub（仅实现 .get / async）。"""
    rec = _Recorder()
    # 直接喂给 HeroSmsProvider；它只调用 client.get(...)
    return rec


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """虚拟时钟：``asyncio.sleep`` 立刻返回，但 provider 内部的 ``_now_seconds``
    会按"sleep 参数"累加；这样 deadline 判定才会真的命中。"""
    import asyncio as _asyncio

    real_sleep = _asyncio.sleep
    state = {"elapsed": 0.0}

    async def _fast(seconds: float) -> None:
        state["elapsed"] += float(seconds or 0)
        await real_sleep(0)

    monkeypatch.setattr(
        "backend.app.services.xhs_auth.hero_sms_provider.asyncio.sleep", _fast
    )

    base_holder = {"v": None}

    def _now() -> float:
        if base_holder["v"] is None:
            base_holder["v"] = _asyncio.get_event_loop().time()
        return base_holder["v"] + state["elapsed"]

    monkeypatch.setattr(
        "backend.app.services.xhs_auth.hero_sms_provider.HeroSmsProvider._now_seconds",
        staticmethod(_now),
    )


def _provider(client, **kwargs) -> HeroSmsProvider:
    defaults = dict(
        api_key="testkey",
        phone_poll_interval_sec=1,
        phone_poll_timeout_sec=5,
        sms_poll_interval_sec=1,
        sms_poll_timeout_sec=5,
        client=client,
    )
    defaults.update(kwargs)
    return HeroSmsProvider(**defaults)


# ---------------------------------------------------------------------------
# acquire_phone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_phone_success(fake_client):
    fake_client.queue(
        "getNumber", _FakeResponse(200, "ACCESS_NUMBER:order123:85291234567")
    )
    p = _provider(fake_client)

    result = await p.acquire_phone()
    assert isinstance(result, PhonePurchase)
    assert result.order_id == "order123"
    assert result.phone == "85291234567"
    assert result.country_code == "HK"

    # getNumber 必须带 service/country/maxPrice/api_key（与 hero-sms 官方示例一致）
    assert fake_client.calls[0]["api_key"] == "testkey"
    assert fake_client.calls[0]["service"] == "qf"
    assert fake_client.calls[0]["country"] == "14"
    assert fake_client.calls[0]["maxPrice"] == "1"
    assert fake_client.calls[0]["action"] == "getNumber"


@pytest.mark.asyncio
async def test_acquire_phone_retries_no_numbers(fake_client):
    fake_client.queue(
        "getNumber",
        _FakeResponse(200, "NO_NUMBERS"),
        _FakeResponse(200, "NO_NUMBERS"),
        _FakeResponse(200, "ACCESS_NUMBER:order9:85299999999"),
    )
    p = _provider(fake_client)

    result = await p.acquire_phone()
    assert result.phone == "85299999999"
    assert len(fake_client.calls) == 3


@pytest.mark.asyncio
async def test_acquire_phone_timeout_raises_no_stock(fake_client):
    """每次都 NO_NUMBERS，超时应抛 SmsNoStockError。"""
    for _ in range(20):
        fake_client.queue("getNumber", _FakeResponse(200, "NO_NUMBERS"))
    p = _provider(fake_client, phone_poll_timeout_sec=0)

    with pytest.raises(SmsNoStockError):
        await p.acquire_phone()


@pytest.mark.asyncio
async def test_acquire_phone_bad_key_raises_auth(fake_client):
    fake_client.queue("getNumber", _FakeResponse(200, "BAD_KEY"))
    p = _provider(fake_client)
    with pytest.raises(SmsAuthError):
        await p.acquire_phone()


@pytest.mark.asyncio
async def test_acquire_phone_unknown_response(fake_client):
    fake_client.queue("getNumber", _FakeResponse(200, "WTF_UNKNOWN"))
    p = _provider(fake_client)
    with pytest.raises(SmsResponseError):
        await p.acquire_phone()


@pytest.mark.asyncio
async def test_acquire_phone_missing_api_key(fake_client, monkeypatch):
    # 测试期望「传空 api_key + 没 env 兜底」时立即 SmsAuthError；
    # 显式 delenv 防止本地 .env 真填了 key 时干扰
    monkeypatch.delenv("SMS_PROVIDER_API_KEY", raising=False)
    p = _provider(fake_client, api_key="")
    with pytest.raises(SmsAuthError):
        await p.acquire_phone()


@pytest.mark.asyncio
async def test_acquire_phone_http_500_transport_error(fake_client):
    fake_client.queue("getNumber", _FakeResponse(503, "Service Unavailable"))
    p = _provider(fake_client)
    from backend.app.services.xhs_auth import SmsTransportError

    with pytest.raises(SmsTransportError):
        await p.acquire_phone()


# ---------------------------------------------------------------------------
# wait_sms_code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_sms_code_success_after_wait(fake_client):
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, "STATUS_WAIT_CODE"),
        _FakeResponse(200, "STATUS_WAIT_CODE"),
        _FakeResponse(200, "STATUS_OK:483921"),
    )
    p = _provider(fake_client)

    res: SmsCodeResult = await p.wait_sms_code("order123")
    assert res.code == "483921"
    assert res.order_id == "order123"
    assert len(fake_client.calls) == 3


@pytest.mark.asyncio
async def test_wait_sms_code_extracts_digits_from_text(fake_client):
    """有些 hero-sms 返回完整短信原文（含数字），需要抽取最长数字串。"""
    fake_client.queue(
        "getAllSms", _FakeResponse(200, "STATUS_OK:你的小红书验证码是 729183，5 分钟内有效")
    )
    p = _provider(fake_client)

    res = await p.wait_sms_code("order_a")
    assert res.code == "729183"


@pytest.mark.asyncio
async def test_wait_sms_code_handles_json_array(fake_client):
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, 'STATUS_OK:["111222","333444"]'),
    )
    p = _provider(fake_client)
    res = await p.wait_sms_code("order_b")
    # 取第一条
    assert res.code == "111222"


@pytest.mark.asyncio
async def test_wait_sms_code_timeout(fake_client):
    for _ in range(20):
        fake_client.queue("getAllSms", _FakeResponse(200, "STATUS_WAIT_CODE"))
    p = _provider(fake_client, sms_poll_timeout_sec=0)
    with pytest.raises(SmsTimeoutError):
        await p.wait_sms_code("order_c")


@pytest.mark.asyncio
async def test_wait_sms_code_cancel(fake_client):
    fake_client.queue("getAllSms", _FakeResponse(200, "STATUS_CANCEL"))
    p = _provider(fake_client)
    with pytest.raises(SmsCancelledError):
        await p.wait_sms_code("order_d")


@pytest.mark.asyncio
async def test_wait_sms_code_empty_order_id(fake_client):
    p = _provider(fake_client)
    with pytest.raises(SmsResponseError):
        await p.wait_sms_code("")


@pytest.mark.asyncio
async def test_wait_sms_code_status_ok_but_no_payload(fake_client):
    fake_client.queue("getAllSms", _FakeResponse(200, "STATUS_OK:"))
    p = _provider(fake_client)
    with pytest.raises(SmsResponseError):
        await p.wait_sms_code("order_e")


# ---------------------------------------------------------------------------
# wait_sms_code: 扩展 retryable + lenient 未知响应
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "waiting_response",
    [
        "STATUS_WAIT_RESEND",
        "STATUS_PROCESS",
        "STATUS_WAITING",
        "ACCESS_RETRY_GET",
        "ACCESS_ACTIVATION",
        "WAIT_CODE",
        "WAIT_SMS",
    ],
)
@pytest.mark.asyncio
async def test_wait_sms_code_treats_extended_waiting_states_as_retryable(
    fake_client, waiting_response
):
    """hero-sms 在号未收到 SMS 期间会返回多种「等待」状态，必须全部当 retryable。"""
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, waiting_response),
        _FakeResponse(200, "STATUS_OK:778899"),
    )
    p = _provider(fake_client, sms_poll_interval_sec=0)
    code = await p.wait_sms_code("order_w")
    assert code.code == "778899"
    sms_calls = [c for c in fake_client.calls if c["action"] == "getAllSms"]
    assert len(sms_calls) == 2  # 等待一次后拿到


@pytest.mark.asyncio
async def test_wait_sms_code_lenient_unknown_response_keeps_polling(fake_client):
    """陌生响应（hero-sms 偶尔返回的非标准串）必须按 retryable 处理，
    避免「点完获取验证码立即失败」。"""
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, "RANDOM_GIBBERISH"),
        _FakeResponse(200, "MOOD_SWING"),
        _FakeResponse(200, "STATUS_OK:445566"),
    )
    p = _provider(fake_client, sms_poll_interval_sec=0)
    code = await p.wait_sms_code("order_l")
    assert code.code == "445566"
    sms_calls = [c for c in fake_client.calls if c["action"] == "getAllSms"]
    assert len(sms_calls) == 3  # 两次未知响应也继续轮询直到拿到


@pytest.mark.asyncio
async def test_wait_sms_code_unknown_response_until_timeout_raises_sms_timeout(
    fake_client,
):
    """如果未知响应一直持续到 deadline，应抛 SMS_TIMEOUT 而非 SMS_RESPONSE_ERROR。"""
    for _ in range(10):
        fake_client.queue("getAllSms", _FakeResponse(200, "MYSTERY_BLOB"))
    p = _provider(fake_client, sms_poll_timeout_sec=0)
    with pytest.raises(SmsTimeoutError) as exc_info:
        await p.wait_sms_code("order_t")
    assert "MYSTERY_BLOB" in str(exc_info.value)


# ---------------------------------------------------------------------------
# release_phone（默认 no-op）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_phone_default_noop(fake_client):
    p = _provider(fake_client)
    assert await p.release_phone("anything") is False


# ---------------------------------------------------------------------------
# 端到端：acquire → wait
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_sms_code_skips_seen_old_code(fake_client):
    """复用号场景：getAllSms 仍返回上次的旧码 → 必须继续轮询，直到新码到达。"""
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, "STATUS_OK:111222"),  # 旧码（在 seen_codes 中）
        _FakeResponse(200, "STATUS_OK:111222"),  # 还是旧码
        _FakeResponse(200, "STATUS_OK:333444"),  # 新码
    )
    p = _provider(fake_client, sms_poll_interval_sec=0)
    code = await p.wait_sms_code("order_reuse", seen_codes=["111222"])
    assert code.code == "333444"
    # 必须发起 3 次 getAllSms 调用：旧、旧、新
    sms_calls = [c for c in fake_client.calls if c["action"] == "getAllSms"]
    assert len(sms_calls) == 3


@pytest.mark.asyncio
async def test_wait_sms_code_seen_only_timeout(fake_client):
    """如果 hero-sms 一直只回旧码到超时，应抛 SMS_TIMEOUT。"""
    for _ in range(10):
        fake_client.queue("getAllSms", _FakeResponse(200, "STATUS_OK:777888"))
    p = _provider(fake_client, sms_poll_timeout_sec=0)
    with pytest.raises(SmsTimeoutError):
        await p.wait_sms_code("order_old", seen_codes=["777888"])


@pytest.mark.asyncio
async def test_full_flow_acquire_then_wait(fake_client):
    fake_client.queue(
        "getNumber",
        _FakeResponse(200, "NO_NUMBERS"),
        _FakeResponse(200, "ACCESS_NUMBER:42:85211112222"),
    )
    fake_client.queue(
        "getAllSms",
        _FakeResponse(200, "STATUS_WAIT_CODE"),
        _FakeResponse(200, "STATUS_OK:559900"),
    )
    p = _provider(fake_client)

    purchase = await p.acquire_phone()
    assert purchase.order_id == "42"
    assert purchase.phone == "85211112222"

    code = await p.wait_sms_code(purchase.order_id)
    assert code.code == "559900"

    # 按 action 分别校验参数：
    # - getNumber 带 service/country/maxPrice
    # - getAllSms 仅带 api_key + id（不带 service/country/maxPrice）
    for call in fake_client.calls:
        assert call["api_key"] == "testkey"
        if call["action"] == "getNumber":
            assert call["service"] == "qf"
            assert call["country"] == "14"
            assert call["maxPrice"] == "1"
            assert "id" not in call
        elif call["action"] == "getAllSms":
            assert call["id"] == "42"
            assert "service" not in call
            assert "country" not in call
            assert "maxPrice" not in call
