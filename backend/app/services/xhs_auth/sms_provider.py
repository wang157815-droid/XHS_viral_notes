"""SMS 接码平台抽象（Phase 3-A）。

设计目标：让"自动重新授权小红书"这个用例不绑死在某一家虚拟号供应商上：

- :class:`SmsProvider` 定义统一接口；
- :class:`HeroSmsProvider`（``hero_sms_provider.py``）是默认实现；
- 后续接入 sms-activate / SMS-Hub 等只需新增子类即可。

接口语义按"购号 → 等验证码 → 释放（可选）"三步：

1. :meth:`acquire_phone` 拉一个临时手机号，返回 :class:`PhonePurchase`；
   响应 ``NO_NUMBERS`` 时由调用方决定要不要轮询重试（hero-sms 短期可能没库存）。
2. :meth:`wait_sms_code` 阻塞轮询验证码；典型超时 5 分钟。
3. :meth:`release_phone` 通知供应商释放手机号；hero-sms 文档明确"不实现亦可"，
   留作可选 hook。

错误一律抛 :class:`SmsProviderError` 的子类，调用方按 ``code`` 分类处理。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class PhonePurchase:
    """成功购到一张虚拟号时的返回值。"""

    order_id: str
    """供应商内部订单号（用于查验证码 / 释放）。"""

    phone: str
    """E.164 数字串，不带 ``+``，可由调用方按运营商规则补前缀。"""

    country_code: str = ""
    """ISO 国家码，用于 UI 展示（如 "HK"、"CN"）；hero-sms country=14 即 HK。"""

    raw: Dict[str, Any] = field(default_factory=dict)
    """原始响应（含 service / cost / 其它字段），调试用。"""


@dataclass
class SmsCodeResult:
    """成功拉到验证码时的返回值。"""

    order_id: str
    code: str
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SmsProviderError(RuntimeError):
    """SmsProvider 通用错误。``code`` 用来在 UI 上分类展示。"""

    code: str = "SMS_PROVIDER_ERROR"

    def __init__(self, message: str, *, code: Optional[str] = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class SmsNoStockError(SmsProviderError):
    """无可用号码（``NO_NUMBERS`` / ``MAX_PRICE_EXCEEDED`` / ``NO_BALANCE``）。

    调用方拿到此异常时可短暂等待后再 ``acquire_phone``，或对最终用户提示"暂无可用虚拟号"。
    """

    code = "SMS_NO_STOCK"


class SmsAuthError(SmsProviderError):
    """API key 错误 / 帐号被封等不可恢复鉴权问题。"""

    code = "SMS_AUTH_ERROR"


class SmsTimeoutError(SmsProviderError):
    """轮询超时未拿到验证码（hero-sms ``STATUS_WAIT_CODE`` 反复返回）。"""

    code = "SMS_TIMEOUT"


class SmsCancelledError(SmsProviderError):
    """供应商或用户主动取消了订单（``STATUS_CANCEL``）。"""

    code = "SMS_CANCELLED"


class SmsTransportError(SmsProviderError):
    """HTTP / 网络层异常。"""

    code = "SMS_TRANSPORT_ERROR"


class SmsResponseError(SmsProviderError):
    """供应商返回了未知 / 不可解析的响应。"""

    code = "SMS_RESPONSE_ERROR"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class SmsProvider(abc.ABC):
    """供应商通用接口。所有方法均为 async。"""

    name: str = "abstract"

    @abc.abstractmethod
    async def acquire_phone(self) -> PhonePurchase:
        """拉一个新的虚拟手机号；无库存时抛 :class:`SmsNoStockError`。"""

    @abc.abstractmethod
    async def wait_sms_code(
        self,
        order_id: str,
        *,
        timeout_seconds: int = 300,
        poll_interval_seconds: int = 60,
        seen_codes: Optional[Iterable[str]] = None,
    ) -> SmsCodeResult:
        """阻塞轮询验证码；超时抛 :class:`SmsTimeoutError`。

        ``seen_codes``：复用同一手机号场景下传入历史已见过的验证码集合；
        实现方需要将这些码视为"旧码"继续轮询，避免误判。
        """

    async def peek_sms_code(
        self, order_id: str
    ) -> Optional[SmsCodeResult]:
        """**单次查询**该 order 是否已有验证码（不轮询、不阻塞、不过滤旧码）。

        语义：
        - 返回 ``None``：order 上**没有**任何验证码 → 该号尚未被消费，可复用
        - 返回 ``SmsCodeResult``：order 上**已经**有过验证码 → 该号已被消费，
          调用方应当放弃复用、重新购号
        - 抛 :class:`SmsCancelledError`：订单已被运营商取消，重新购号
        - 抛 :class:`SmsAuthError`：API key 失效，应中止整个流程

        默认基于 :meth:`wait_sms_code` 实现：``timeout_seconds=0`` 单次轮询。
        子类（如 ``HeroSmsProvider``）应当提供更高效的实现，避免 wait_sms_code
        的 retry 循环逻辑。
        """
        try:
            return await self.wait_sms_code(
                order_id, timeout_seconds=0, poll_interval_seconds=1
            )
        except SmsTimeoutError:
            return None

    async def release_phone(self, order_id: str) -> bool:  # pragma: no cover - 默认 no-op
        """可选：通知供应商释放号码。hero-sms 默认不调用。

        默认实现返回 False（未实现），子类可覆盖。
        """
        return False
