"""SmsLoginDriver 抽象（Phase 3-B）。

本文件只定义"自动 SMS 登录小红书"过程中浏览器侧需要的最小动作集合，
不绑定具体浏览器实现。这样：

- :class:`SmsLoginService` 的状态机可以独立测试（注入 fake driver）；
- 真实 :class:`PlaywrightSmsLoginDriver`（``playwright_sms_login_driver.py``）
  按本接口实现一份；后续若需要切换到 Selenium / Puppeteer 也只需新增子类。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass
class CookieSnapshot:
    """driver 抽到的 cookie 快照。"""

    cookies_str: str
    raw_count: int = 0


class SmsLoginDriver(abc.ABC):
    """浏览器层最小动作接口。"""

    @abc.abstractmethod
    async def open_login_page(self) -> None:
        """启动浏览器并打开"手机号登录"标签。"""

    @abc.abstractmethod
    async def fill_phone(self, *, country_code: str, phone: str) -> None:
        """选择国家区号 + 填手机号；不点发送按钮。"""

    @abc.abstractmethod
    async def click_send_sms(self) -> None:
        """点击「获取验证码」按钮（首次发送）。"""

    async def click_resend_sms(self) -> None:
        """点击「重新获取」按钮（倒计时归零后再次发送）。

        默认实现：fallback 调 :meth:`click_send_sms`。
        子类可重写以走真正的「重新获取」selector，倒计时归零后小红书 UI
        通常会让按钮文本变回「获取验证码」，因此 fallback 是安全的。
        """
        await self.click_send_sms()

    @abc.abstractmethod
    async def fill_and_submit_sms(self, code: str) -> None:
        """把验证码填入并提交（点登录或回车触发）。"""

    @abc.abstractmethod
    async def wait_for_login_success(self, *, timeout_seconds: int = 60) -> CookieSnapshot:
        """等待登录跳转 / cookie 写入；超时抛 :class:`TimeoutError`。"""

    @abc.abstractmethod
    async def cleanup(self) -> None:
        """关浏览器。Service 取消 session 时调用。"""

    # ----- 可选 hooks -----
    async def screenshot(self) -> Optional[bytes]:  # pragma: no cover - 默认 no-op
        """便于排错；返回 None 表示不支持截图。"""
        return None
