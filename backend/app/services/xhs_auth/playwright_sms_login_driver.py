"""PlaywrightSmsLoginDriver（Phase 3-B）。

注意：本驱动负责"浏览器侧自动操作小红书登录页"的具体步骤。它的 DOM
selector 必须与小红书前端结构吻合；当前小红书 PC 登录页的元素经常
A/B 测试，selector 必然要随真实联调更新。

实现策略：
- 复用 ``viral_agent.services.auth.qrcode_login_service`` 已有的 Playwright
  上下文启动逻辑（通过新建 persistent context）。
- 所有 selector 优先尝试 environment override（``XHS_SMS_LOGIN_*``）；
  没有 override 时按当前主流登录页的常见命名做最佳猜测，并记录 warning。
- 获取 cookie 时直接读 context.cookies()，转换为 ``cookie=value; ...`` 串。

未来若需要彻底自动化（含切换"手机号登录" Tab、选择国家区号等），
建议把这层 Driver 进一步拆成「打开页面 / 切换 Tab / 选区号 / 填号 /
点发送 / 填验证码 / 等成功」7 步原语，把每个 selector 都暴露为
配置项；本文件已留出 ``_SELECTORS`` 字典作为锚点。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from .sms_login_driver import CookieSnapshot, SmsLoginDriver


# ---------------------------------------------------------------------------
# Selectors（环境变量 + 默认）
# ---------------------------------------------------------------------------

_SELECTORS: Dict[str, str] = {
    # 切换到「手机号登录」Tab。优先 :text-is，老 UI 里这个文案稳定。
    "phone_tab": os.getenv("XHS_SMS_TAB_SELECTOR")
    or ":text-is(\"手机号登录\")",
    # 国家区号下拉
    "country_selector": os.getenv("XHS_SMS_COUNTRY_SELECTOR")
    or ".country-flag, .country-code, [class*='country']",
    "country_option_template": os.getenv("XHS_SMS_COUNTRY_OPTION")
    or ":text(\"+852\")",  # Hong Kong（用户固定）；其他国家走 override
    # 手机号输入框
    "phone_input": os.getenv("XHS_SMS_PHONE_INPUT")
    or "input[placeholder*='手机号'], input[type='tel'], input[name='phone']",
    # 「获取验证码 / 发送验证码」按钮
    "send_code_button": os.getenv("XHS_SMS_SEND_BUTTON")
    or ":text-is(\"获取验证码\"), :text-is(\"发送验证码\")",
    # 验证码输入框
    "sms_input": os.getenv("XHS_SMS_CODE_INPUT")
    or "input[placeholder*='验证码'], input[name='code'], input[name='verifyCode']",
    # 登录提交按钮
    "submit_button": os.getenv("XHS_SMS_SUBMIT_BUTTON")
    or ":text-is(\"登录\"), button[type='submit']",
}

_LOGIN_URL = os.getenv("XHS_LOGIN_START_URL") or "https://www.xiaohongshu.com/explore"
_PROFILE_DIR = (
    Path(os.getenv("XHS_SMS_BROWSER_PROFILE_DIR") or "datas/browsers/sms_login")
    .expanduser()
    .resolve()
)
_HEADLESS = (os.getenv("XHS_SMS_HEADLESS") or "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
_NAV_TIMEOUT_MS = int(os.getenv("XHS_SMS_NAV_TIMEOUT_MS") or "30000")
_DEFAULT_SUCCESS_URL_HINT = "/explore"  # 登录成功后会跳转 explore


class PlaywrightSmsLoginDriver(SmsLoginDriver):
    """基于 Playwright 的 SMS 自动登录驱动。"""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._closed = False

    # ------------------------------------------------------------------
    # 接口实现
    # ------------------------------------------------------------------

    async def open_login_page(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - 缺包时抛出
            raise RuntimeError(
                "Playwright 未安装。运行 `pip install playwright` 并执行 `playwright install chromium`。"
            ) from exc

        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=_HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        await self._page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        # 切到「手机号登录」标签
        await self._safe_click(_SELECTORS["phone_tab"], optional=True)

    async def fill_phone(self, *, country_code: str, phone: str) -> None:
        page = self._require_page()
        # 切换国家代码（仅当默认不是目标）
        await self._safe_click(_SELECTORS["country_selector"], optional=True)
        # +852 / +86 / +1 ...
        if country_code:
            cc = country_code.strip()
            template = _SELECTORS["country_option_template"]
            # 模板里出现 +852 时按 country_code 实际值替换
            if "+" not in cc:
                cc_label = self._country_iso_to_label(cc)
            else:
                cc_label = cc
            selector = template.replace("+852", cc_label)
            await self._safe_click(selector, optional=True)
        # 填手机号
        try:
            await page.fill(_SELECTORS["phone_input"], phone, timeout=_NAV_TIMEOUT_MS)
        except Exception as exc:
            raise RuntimeError(f"填写手机号失败: {exc}") from exc

    async def click_send_sms(self) -> None:
        page = self._require_page()
        try:
            await page.click(_SELECTORS["send_code_button"], timeout=_NAV_TIMEOUT_MS)
        except Exception as exc:
            raise RuntimeError(f"点击「获取验证码」失败: {exc}") from exc

    async def fill_and_submit_sms(self, code: str) -> None:
        page = self._require_page()
        try:
            await page.fill(_SELECTORS["sms_input"], code, timeout=_NAV_TIMEOUT_MS)
        except Exception as exc:
            raise RuntimeError(f"填写验证码失败: {exc}") from exc
        # 提交：尝试点提交按钮，失败回退按 Enter
        if not await self._safe_click(_SELECTORS["submit_button"], optional=True):
            try:
                await page.keyboard.press("Enter")
            except Exception as exc:
                raise RuntimeError(f"提交验证码失败: {exc}") from exc

    async def wait_for_login_success(
        self, *, timeout_seconds: int = 60
    ) -> CookieSnapshot:
        page = self._require_page()
        end = asyncio.get_event_loop().time() + max(5, timeout_seconds)
        while asyncio.get_event_loop().time() < end:
            try:
                if _DEFAULT_SUCCESS_URL_HINT in (page.url or ""):
                    cookies = await self._collect_cookies()
                    if cookies:
                        return cookies
            except Exception:
                pass
            await asyncio.sleep(2)
        raise TimeoutError("等待登录跳转超时")

    async def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._context:
                await self._context.close()
        except Exception as exc:
            logger.debug(f"PlaywrightSmsLoginDriver close context 异常: {exc}")
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.debug(f"PlaywrightSmsLoginDriver stop pw 异常: {exc}")
        self._context = None
        self._page = None
        self._playwright = None

    async def screenshot(self) -> Optional[bytes]:
        if not self._page:
            return None
        try:
            return await self._page.screenshot(full_page=True)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("Playwright 页面尚未初始化，请先调用 open_login_page")
        return self._page

    async def _safe_click(self, selector: str, *, optional: bool) -> bool:
        page = self._require_page()
        try:
            await page.click(selector, timeout=3000)
            return True
        except Exception as exc:
            if optional:
                logger.debug(f"selector 可选点击失败 selector={selector} err={exc}")
                return False
            raise

    async def _collect_cookies(self) -> Optional[CookieSnapshot]:
        if self._context is None:
            return None
        try:
            cookies = await self._context.cookies()
        except Exception as exc:
            logger.warning(f"读取 cookies 失败: {exc}")
            return None
        if not cookies:
            return None
        # 拼接成 "k1=v1; k2=v2" 形式
        pairs = []
        for c in cookies:
            name = c.get("name") or ""
            value = c.get("value") or ""
            if not name:
                continue
            pairs.append(f"{name}={value}")
        return CookieSnapshot(cookies_str="; ".join(pairs), raw_count=len(cookies))

    @staticmethod
    def _country_iso_to_label(iso: str) -> str:
        mapping = {
            "HK": "+852",
            "CN": "+86",
            "US": "+1",
            "GB": "+44",
        }
        return mapping.get(iso.upper(), iso)
