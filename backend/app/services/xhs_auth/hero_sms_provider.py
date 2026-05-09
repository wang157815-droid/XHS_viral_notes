"""hero-sms 接码平台实现（Phase 3-A）。

API 约定（基于用户提供的接口规格 + sms-activate 兼容协议）::

    GET https://hero-sms.com/stubs/handler_api.php
        ?api_key=<API_KEY>
        &service=qf            # 小红书
        &country=14            # 14=Hong Kong（用户固定值）
        &maxPrice=1            # 最高接受价格（用户固定）
        &action=getNumber      # 或 getAllSms
        &id=<order_id>         # getAllSms 时必填

响应是文本，常见值：

- ``getNumber``::

    ACCESS_NUMBER:<order_id>:<phone>     成功
    NO_NUMBERS                           暂无库存
    MAX_PRICE_EXCEEDED                   maxPrice 太低
    NO_BALANCE                           余额不足
    BAD_KEY / BANNED                     鉴权问题

- ``getAllSms``::

    STATUS_OK:<code>                     成功
    STATUS_OK:["c1", "c2"]               成功（多条短信）
    STATUS_WAIT_CODE                     验证码尚未到达
    STATUS_CANCEL                        订单被取消
    其它                                 未知错误

文档明确"无需 setStatus 释放手机号，hero-sms 自动 cleanup"，所以
:meth:`release_phone` 默认 no-op。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

from .sms_provider import (
    PhonePurchase,
    SmsAuthError,
    SmsCancelledError,
    SmsCodeResult,
    SmsNoStockError,
    SmsProvider,
    SmsResponseError,
    SmsTimeoutError,
    SmsTransportError,
)


_DEFAULT_BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
_DEFAULT_SERVICE = "qf"  # 小红书
_DEFAULT_COUNTRY = "14"  # Hong Kong
_DEFAULT_MAX_PRICE = "1"

# 用户给定的轮询/超时上限
_DEFAULT_PHONE_POLL_INTERVAL_SEC = 8
_DEFAULT_PHONE_POLL_TIMEOUT_SEC = 120
_DEFAULT_SMS_POLL_INTERVAL_SEC = 60
_DEFAULT_SMS_POLL_TIMEOUT_SEC = 300

_HTTP_TIMEOUT_SEC = 15


_NO_STOCK_RESPONSES = {"NO_NUMBERS", "MAX_PRICE_EXCEEDED", "NO_BALANCE"}
_AUTH_ERROR_RESPONSES = {"BAD_KEY", "BANNED", "WRONG_USER_KEY"}
_RETRYABLE_SMS_RESPONSES = {"STATUS_WAIT_CODE", "STATUS_WAIT_RETRY"}


class HeroSmsProvider(SmsProvider):
    """hero-sms 实现。

    入参约定：
    - ``api_key``：必填；空则从 ``SMS_PROVIDER_API_KEY`` env 读取。
    - ``base_url``、``service``、``country``、``max_price``：用户给定固定值，
      允许通过 env / 构造函数覆盖以便切换接码服务（如换运营商）。
    - ``client``：测试时可注入 ``httpx.AsyncClient``，避免真实请求。
    """

    name = "hero_sms"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        service: Optional[str] = None,
        country: Optional[str] = None,
        max_price: Optional[str] = None,
        phone_poll_interval_sec: Optional[int] = None,
        phone_poll_timeout_sec: Optional[int] = None,
        sms_poll_interval_sec: Optional[int] = None,
        sms_poll_timeout_sec: Optional[int] = None,
        http_timeout_sec: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = (api_key or os.getenv("SMS_PROVIDER_API_KEY") or "").strip()
        self._base_url = (base_url or os.getenv("SMS_PROVIDER_BASE_URL") or _DEFAULT_BASE_URL).strip()
        self._service = (service or os.getenv("SMS_PROVIDER_SERVICE") or _DEFAULT_SERVICE).strip()
        self._country = (country or os.getenv("SMS_PROVIDER_COUNTRY") or _DEFAULT_COUNTRY).strip()
        self._max_price = (max_price or os.getenv("SMS_PROVIDER_MAX_PRICE") or _DEFAULT_MAX_PRICE).strip()

        # 用 ``is None`` 判断而非 ``or``：测试中常传 0 表示"立刻超时"，``or`` 会
        # 把 0 误当成默认值。
        phone_interval = (
            _DEFAULT_PHONE_POLL_INTERVAL_SEC
            if phone_poll_interval_sec is None
            else phone_poll_interval_sec
        )
        phone_timeout = (
            _DEFAULT_PHONE_POLL_TIMEOUT_SEC
            if phone_poll_timeout_sec is None
            else phone_poll_timeout_sec
        )
        sms_interval = (
            _DEFAULT_SMS_POLL_INTERVAL_SEC
            if sms_poll_interval_sec is None
            else sms_poll_interval_sec
        )
        sms_timeout = (
            _DEFAULT_SMS_POLL_TIMEOUT_SEC
            if sms_poll_timeout_sec is None
            else sms_poll_timeout_sec
        )
        self._phone_poll_interval = max(1, phone_interval)
        self._phone_poll_timeout = max(0, phone_timeout)
        self._sms_poll_interval = max(1, sms_interval)
        self._sms_poll_timeout = max(0, sms_timeout)
        self._http_timeout = (
            _HTTP_TIMEOUT_SEC if http_timeout_sec is None else http_timeout_sec
        )

        self._client = client  # None → 每次新建短连接；测试可注入 mock
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # SmsProvider 接口
    # ------------------------------------------------------------------

    async def acquire_phone(self) -> PhonePurchase:
        """轮询 ``getNumber`` 直到拿到号码或抛错。"""
        if not self._api_key:
            raise SmsAuthError("SMS_PROVIDER_API_KEY 未配置", code="SMS_AUTH_ERROR")

        deadline = self._loop_deadline(self._phone_poll_timeout)
        last_no_stock_msg = ""
        attempts = 0

        # hero-sms 官方示例：getNumber 需要 service / country / maxPrice
        getnumber_params = {
            "action": "getNumber",
            "service": self._service,
            "country": self._country,
            "maxPrice": self._max_price,
        }

        while True:
            attempts += 1
            text = await self._call(getnumber_params)
            tag, payload = _parse_text_response(text)

            if tag == "ACCESS_NUMBER":
                order_id, phone = _split_access_number_payload(payload, raw=text)
                logger.info(
                    f"[hero_sms] 购号成功: order_id={order_id} phone=…{phone[-4:]}（attempts={attempts}）"
                )
                return PhonePurchase(
                    order_id=order_id,
                    phone=phone,
                    country_code=_country_iso_from_code(self._country),
                    raw={"text": text, "service": self._service},
                )

            if tag in _AUTH_ERROR_RESPONSES:
                raise SmsAuthError(f"hero-sms 鉴权失败：{text}", code="SMS_AUTH_ERROR")

            if tag in _NO_STOCK_RESPONSES:
                last_no_stock_msg = text
                if self._now_seconds() >= deadline:
                    raise SmsNoStockError(
                        f"hero-sms 暂无可用虚拟号（最后响应：{last_no_stock_msg}）",
                        code="SMS_NO_STOCK",
                    )
                logger.info(
                    f"[hero_sms] 暂无库存（{text}），{self._phone_poll_interval}s 后重试"
                )
                await asyncio.sleep(self._phone_poll_interval)
                continue

            # 未知响应：直接抛
            raise SmsResponseError(
                f"hero-sms 返回未知响应：{text}", code="SMS_RESPONSE_ERROR"
            )

    async def wait_sms_code(
        self,
        order_id: str,
        *,
        timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
    ) -> SmsCodeResult:
        if not order_id:
            raise SmsResponseError("order_id 不能为空", code="SMS_RESPONSE_ERROR")
        if not self._api_key:
            raise SmsAuthError("SMS_PROVIDER_API_KEY 未配置", code="SMS_AUTH_ERROR")

        timeout = timeout_seconds or self._sms_poll_timeout
        interval = poll_interval_seconds or self._sms_poll_interval
        deadline = self._loop_deadline(timeout)

        attempts = 0
        while True:
            attempts += 1
            text = await self._call({"action": "getAllSms", "id": order_id})
            tag, payload = _parse_text_response(text)

            if tag == "STATUS_OK":
                code = _extract_code_from_payload(payload, raw=text)
                logger.info(
                    f"[hero_sms] 拉到验证码 order_id={order_id} attempts={attempts}"
                )
                return SmsCodeResult(order_id=order_id, code=code, raw={"text": text})

            if tag in _RETRYABLE_SMS_RESPONSES:
                if self._now_seconds() >= deadline:
                    raise SmsTimeoutError(
                        f"等待验证码超时（{timeout}s）", code="SMS_TIMEOUT"
                    )
                logger.debug(
                    f"[hero_sms] 等待验证码（{text}），{interval}s 后再查 order_id={order_id}"
                )
                await asyncio.sleep(interval)
                continue

            if tag == "STATUS_CANCEL":
                raise SmsCancelledError(
                    f"订单被取消：order_id={order_id}", code="SMS_CANCELLED"
                )

            if tag in _AUTH_ERROR_RESPONSES:
                raise SmsAuthError(f"hero-sms 鉴权失败：{text}", code="SMS_AUTH_ERROR")

            raise SmsResponseError(
                f"hero-sms 返回未知响应：{text}", code="SMS_RESPONSE_ERROR"
            )

    async def release_phone(self, order_id: str) -> bool:
        # 文档明确 hero-sms 不需要主动释放，留作 no-op 以满足接口
        return False

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _call(self, extra_params: Dict[str, str]) -> str:
        # 仅 api_key 是所有 action 共用；service/country/maxPrice 按 action
        # 自己拼（hero-sms 官方示例：getAllSms 只需要 id + api_key）。
        params = {
            "api_key": self._api_key,
            **extra_params,
        }
        try:
            if self._client is not None:
                resp = await self._client.get(self._base_url, params=params, timeout=self._http_timeout)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(self._base_url, params=params, timeout=self._http_timeout)
        except httpx.HTTPError as exc:
            raise SmsTransportError(
                f"hero-sms 请求失败：{exc}", code="SMS_TRANSPORT_ERROR"
            ) from exc

        if resp.status_code >= 500:
            raise SmsTransportError(
                f"hero-sms HTTP {resp.status_code}: {resp.text[:200]}",
                code="SMS_TRANSPORT_ERROR",
            )
        if resp.status_code >= 400:
            raise SmsResponseError(
                f"hero-sms HTTP {resp.status_code}: {resp.text[:200]}",
                code="SMS_RESPONSE_ERROR",
            )

        return (resp.text or "").strip()

    @staticmethod
    def _loop_deadline(timeout_sec: int) -> float:
        loop = asyncio.get_event_loop()
        return loop.time() + max(0, timeout_sec)

    @staticmethod
    def _now_seconds() -> float:
        return asyncio.get_event_loop().time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_text_response(text: str) -> Tuple[str, str]:
    """``ACCESS_NUMBER:123:8520`` → ``("ACCESS_NUMBER", "123:8520")``。

    无冒号时 payload 为空字符串。
    """
    if not text:
        return "", ""
    head, sep, tail = text.partition(":")
    if not sep:
        return text.strip(), ""
    return head.strip(), tail.strip()


def _split_access_number_payload(payload: str, *, raw: str) -> Tuple[str, str]:
    parts = payload.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise SmsResponseError(
            f"无法解析 ACCESS_NUMBER 响应: {raw}", code="SMS_RESPONSE_ERROR"
        )
    return parts[0].strip(), parts[1].strip()


def _extract_code_from_payload(payload: str, *, raw: str) -> str:
    """``STATUS_OK:1234`` 或 ``STATUS_OK:["1234","5678"]`` → 取第一条 / 唯一一条。"""
    if not payload:
        raise SmsResponseError(f"STATUS_OK 但缺少验证码: {raw}", code="SMS_RESPONSE_ERROR")

    # JSON 数组
    if payload.startswith("["):
        try:
            parsed = json.loads(payload)
        except Exception as exc:
            raise SmsResponseError(
                f"无法解析 STATUS_OK JSON: {raw} ({exc})", code="SMS_RESPONSE_ERROR"
            ) from exc
        if not isinstance(parsed, list) or not parsed:
            raise SmsResponseError(
                f"STATUS_OK 数组为空: {raw}", code="SMS_RESPONSE_ERROR"
            )
        first = str(parsed[0]).strip()
        return _normalize_code(first)

    return _normalize_code(payload)


def _normalize_code(value: str) -> str:
    """从短信原文中抽取连续数字（hero-sms 有时返回完整短信文本）。"""
    s = (value or "").strip()
    if not s:
        raise SmsResponseError("验证码内容为空", code="SMS_RESPONSE_ERROR")
    if s.isdigit():
        return s
    digits = re.findall(r"\d{4,8}", s)
    if not digits:
        raise SmsResponseError(f"验证码中找不到数字: {value}", code="SMS_RESPONSE_ERROR")
    # 取最长一段，避免误抓时间戳里的短数字
    digits.sort(key=len, reverse=True)
    return digits[0]


def _country_iso_from_code(code: str) -> str:
    """把 hero-sms 数字 country code 翻译成常用 ISO 缩写（仅用于 UI 显示）。"""
    mapping = {
        "0": "RU",
        "1": "UA",
        "6": "ID",
        "10": "VN",
        "12": "US",
        "14": "HK",
        "16": "GB",
        "22": "IN",
    }
    return mapping.get(code.strip(), "")
