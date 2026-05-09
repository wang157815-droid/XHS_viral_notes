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
    # 切换到「手机号登录」Tab。多数情况下右侧表单已默认显示，不需要切；
    # 仅在 A/B 流量出现独立 tab 时点一下。所有 _safe_click 都会 optional 处理。
    "phone_tab": os.getenv("XHS_SMS_TAB_SELECTOR")
    or ":text-is(\"手机号登录\")",
    # 国家区号触发器：截图里是「+1▼」按钮，但 DOM 上几乎不会是裸 <button>，
    # 更可能是 <div>/<span>/带 role=combobox 容器包着 "+1" 文本节点。
    # 用 :text-is 精确匹配文字节点（能命中任何标签），点击会冒泡触发父容器。
    "country_selector": os.getenv("XHS_SMS_COUNTRY_SELECTOR")
    or (
        ":text-is(\"+1\"), :text-is(\"+86\"), :text-is(\"+852\"), "
        ":text-is(\"+44\"), [role='combobox'], "
        "[class*='country' i], [class*='dial' i], [class*='area-code' i], "
        ".reds-select__trigger, .reds-select-trigger"
    ),
    # 国家区号下拉项；模板中 +852 会按 country_code 实际值替换。
    # :text-is 精确匹配，避免误中"+1 (US)"这种长文本。
    "country_option_template": os.getenv("XHS_SMS_COUNTRY_OPTION")
    or ":text-is(\"+852\")",
    # 手机号输入框（placeholder=「请输入手机号」）
    "phone_input": os.getenv("XHS_SMS_PHONE_INPUT")
    or "input[placeholder*='手机号'], input[type='tel'], input[name='phone']",
    # 「获取验证码 / 发送验证码」按钮
    "send_code_button": os.getenv("XHS_SMS_SEND_BUTTON")
    or ":text-is(\"获取验证码\"), :text-is(\"发送验证码\"), button:has-text(\"获取验证码\")",
    # 验证码输入框（placeholder=「输入验证码」）
    "sms_input": os.getenv("XHS_SMS_CODE_INPUT")
    or "input[placeholder*='验证码'], input[name='code'], input[name='verifyCode']",
    # 登录提交大红按钮；显式排除「手机号登录」tab 文案，避免误匹配
    "submit_button": os.getenv("XHS_SMS_SUBMIT_BUTTON")
    or (
        "button:has-text(\"登录\"):not(:has-text(\"手机号\"))"
        ":not(:has-text(\"协议\")):not(:has-text(\"政策\")), "
        "button[type='submit']"
    ),
}

# 截图诊断目录：fill_phone / click_send_sms 失败时自动写入
_SCREENSHOT_DIR = (
    Path(os.getenv("XHS_SMS_SCREENSHOT_DIR") or "datas/sms_screenshots")
    .expanduser()
    .resolve()
)
_DEBUG_DUMP = (os.getenv("XHS_SMS_DEBUG_DUMP") or "").strip().lower() in (
    "1",
    "true",
    "yes",
)

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

        # 1) 切国家区号下拉。selector 失败不致命（optional），但全程打日志 +
        #    截图，便于联调时定位真实 DOM 结构。
        cc_label = ""
        country_picked = False
        if country_code:
            cc = country_code.strip()
            cc_label = cc if cc.startswith("+") else self._country_iso_to_label(cc)
            logger.info(f"[sms_login] 准备切换国家码到 {cc_label}")
            country_picked = await self._switch_country_code(cc_label)
            if not country_picked:
                # 国家码切换失败：写截图 + 输出 DOM 摘要，让用户能看清 selector 应该怎么写
                await self._diagnostic_dump("country_code_failed", reason=f"未能选中 {cc_label}")
                logger.warning(
                    f"[sms_login] ⚠ 国家码 {cc_label} 切换失败，"
                    f"将直接填手机号；小红书默认 +1 可能导致校验失败。"
                    f"截图保存在 {_SCREENSHOT_DIR}，请检查后用 "
                    f"XHS_SMS_COUNTRY_SELECTOR / XHS_SMS_COUNTRY_OPTION 环境变量覆盖。"
                )
        else:
            logger.warning(
                "[sms_login] ⚠ country_code 为空，跳过国家码切换。"
                "（这通常是配置或 reservation 字段丢失，请检查日志。）"
            )

        # 2) 剥离国家拨号前缀，只填本地号（前提是国家码切换成功；如果切换失败
        #    保险起见仍剥前缀，这样万一小红书自动识别国家码也能正常发码）
        local_phone = self._strip_dial_prefix(phone, country_code=country_code)
        logger.info(
            f"[sms_login] 填入手机号 country={cc_label or country_code or '(none)'} "
            f"country_picked={country_picked} "
            f"raw=…{phone[-4:]} local=…{local_phone[-4:]} (len={len(local_phone)})"
        )
        try:
            await page.fill(
                _SELECTORS["phone_input"], local_phone, timeout=_NAV_TIMEOUT_MS
            )
        except Exception as exc:
            await self._diagnostic_dump("phone_input_failed", reason=str(exc))
            raise RuntimeError(f"填写手机号失败: {exc}") from exc

    async def _switch_country_code(self, cc_label: str) -> bool:
        """切国家码下拉：尝试 trigger × option 多种 selector 组合 + 重试。

        返回 True 表示成功选中，False 表示尝试了所有 selector / 重试都没命中。
        """
        page = self._require_page()
        template = _SELECTORS["country_option_template"]
        option_selector = template.replace("+852", cc_label)

        # 最多两轮尝试：第一轮失败后等 1s 让 modal 完整渲染再试一次
        for attempt in (1, 2):
            opened = await self._safe_click(
                _SELECTORS["country_selector"], optional=True
            )
            if not opened:
                logger.info(
                    f"[sms_login] attempt={attempt} 国家码触发器 click 未命中 "
                    f"(selector={_SELECTORS['country_selector']!r})"
                )
            else:
                logger.info(
                    f"[sms_login] attempt={attempt} 国家码触发器已点击，等待下拉渲染"
                )
                try:
                    await page.wait_for_timeout(400)
                except Exception:
                    pass
                picked = await self._safe_click(option_selector, optional=True)
                if picked:
                    logger.info(f"[sms_login] ✓ 已选 {cc_label}")
                    return True
                logger.info(
                    f"[sms_login] attempt={attempt} 选项 {cc_label} 未命中 "
                    f"(selector={option_selector!r})"
                )
            # 第一轮失败 → 等 modal 重新渲染再试
            if attempt == 1:
                try:
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass
        return False

    async def _diagnostic_dump(self, step: str, *, reason: str = "") -> None:
        """selector 失败时自动落盘一份截图 + 可选 DOM 片段，让联调时看清现场。

        - 截图：``datas/sms_screenshots/<step>_<timestamp>.png``
        - DOM 摘要：仅在 ``XHS_SMS_DEBUG_DUMP=true`` 时写入相邻的 ``.html`` 文件
        """
        if self._page is None:
            return
        try:
            _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.debug(f"[sms_login] 创建截图目录失败: {exc}")
            return
        from datetime import datetime as _dt

        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        png_path = _SCREENSHOT_DIR / f"{step}_{ts}.png"
        try:
            await self._page.screenshot(path=str(png_path), full_page=True)
            logger.warning(
                f"[sms_login] ⚠ {step} 失败 ({reason})，截图已保存到 {png_path}"
            )
        except Exception as exc:
            logger.debug(f"[sms_login] 截图保存失败: {exc}")
        if _DEBUG_DUMP:
            try:
                html = await self._page.content()
                html_path = _SCREENSHOT_DIR / f"{step}_{ts}.html"
                html_path.write_text(html[:200_000], encoding="utf-8")
                logger.warning(
                    f"[sms_login] DOM 已 dump 到 {html_path} (前 200KB)"
                )
            except Exception as exc:
                logger.debug(f"[sms_login] DOM dump 失败: {exc}")

    async def click_send_sms(self) -> None:
        page = self._require_page()
        try:
            await page.click(_SELECTORS["send_code_button"], timeout=_NAV_TIMEOUT_MS)
            logger.info("[sms_login] ✓ 已点击「获取验证码」按钮")
        except Exception as exc:
            await self._diagnostic_dump("send_sms_failed", reason=str(exc))
            raise RuntimeError(f"点击「获取验证码」失败: {exc}") from exc

    async def fill_and_submit_sms(self, code: str) -> None:
        page = self._require_page()
        try:
            await page.fill(_SELECTORS["sms_input"], code, timeout=_NAV_TIMEOUT_MS)
            logger.info(f"[sms_login] ✓ 已填入验证码 …{code[-2:]}")
        except Exception as exc:
            await self._diagnostic_dump("sms_input_failed", reason=str(exc))
            raise RuntimeError(f"填写验证码失败: {exc}") from exc
        # 提交：尝试点提交按钮，失败回退按 Enter
        submitted = await self._safe_click(_SELECTORS["submit_button"], optional=True)
        if submitted:
            logger.info("[sms_login] ✓ 已点击「登录」按钮")
        else:
            logger.info("[sms_login] 未命中登录按钮，回退按 Enter 提交")
            try:
                await page.keyboard.press("Enter")
            except Exception as exc:
                await self._diagnostic_dump("submit_failed", reason=str(exc))
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

    @staticmethod
    def _country_iso_to_dial(iso: str) -> str:
        """ISO 缩写 → 纯数字拨号前缀（无 +）。"""
        mapping = {
            "HK": "852",
            "CN": "86",
            "US": "1",
            "GB": "44",
            "TW": "886",
            "MO": "853",
            "SG": "65",
            "MY": "60",
        }
        return mapping.get((iso or "").upper(), "")

    @classmethod
    def _strip_dial_prefix(cls, phone: str, *, country_code: str) -> str:
        """从 hero-sms 返回的完整号码 (如 "85291234567") 中剥离拨号前缀，
        只保留小红书输入框需要的本地号 (如 "91234567")。

        策略：
        - 优先用 ``country_code``（ISO 或 +852）解出 dial code 数字，去前缀；
        - 若 country_code 缺失，尝试常见前缀（852/86/1/44）启发式去除；
        - 全数字检查：剥离结果必须是 6~12 位数字，否则回退原值（避免误删）。
        """
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        if not digits:
            return phone or ""

        candidates: list[str] = []
        cc = (country_code or "").strip()
        if cc.startswith("+"):
            candidates.append(cc[1:])
        elif cc:
            dial = cls._country_iso_to_dial(cc)
            if dial:
                candidates.append(dial)
        # 兜底：常见接码服务返回的号都偏向 HK/CN/US/UK
        for fallback in ("852", "86", "1", "44"):
            if fallback not in candidates:
                candidates.append(fallback)

        for prefix in candidates:
            if digits.startswith(prefix) and len(digits) > len(prefix):
                local = digits[len(prefix):]
                if 6 <= len(local) <= 12:
                    return local
        return digits
