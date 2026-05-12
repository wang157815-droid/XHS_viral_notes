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
    or ":text-is(\"手机号登录\"), :text-is(\"Phone\"), :text-is(\"Phone number\"), :text-is(\"Log in with phone\")",
    # 国家区号触发器：截图里是「+1▼」按钮，但 DOM 上几乎不会是裸 <button>，
    # 更可能是 <div>/<span>/带 role=combobox 容器包着 "+1" 文本节点。
    # 用 :text-is 精确匹配文字节点（能命中任何标签），点击会冒泡触发父容器。
    "country_selector": os.getenv("XHS_SMS_COUNTRY_SELECTOR")
    or (
        ":text-is(\"+1\"), :text-is(\"+86\"), :text-is(\"+852\"), "
        ":text-is(\"+44\"), button:has-text(\"+1\"), button:has-text(\"+86\"), "
        "button:has-text(\"+852\"), button:has-text(\"+44\"), [role='combobox'], "
        "[aria-haspopup='listbox'], [role='button']:has-text(\"+\"), "
        "[class*='country' i], [class*='dial' i], [class*='area-code' i], "
        ".reds-select__trigger, .reds-select-trigger"
    ),
    # 国家区号下拉项；模板中 +852 会按 country_code 实际值替换。
    # :text-is 精确匹配，避免误中"+1 (US)"这种长文本。
    "country_option_template": os.getenv("XHS_SMS_COUNTRY_OPTION")
    or (
        ":text-is(\"+852\"), :text(\"+852\"), [role='option']:has-text(\"+852\"), "
        "li:has-text(\"+852\"), button:has-text(\"+852\")"
    ),
    # 国家码下拉打开后内置的搜索框（截图: placeholder="搜索国家/地区"）。
    # 长列表+虚拟滚动场景下，直接 :text-is 选项无法命中（DOM 里没渲染出来），
    # 必须先用这个搜索框过滤到只剩目标行再点。
    "country_search_input": os.getenv("XHS_SMS_COUNTRY_SEARCH")
    or (
        "input[placeholder*='搜索']"
        ", input[placeholder*='国家']"
        ", input[placeholder*='地区']"
        ", input[placeholder*='Search' i]"
        ", input[placeholder*='country' i]"
        ", input[placeholder*='region' i]"
    ),
    # 手机号输入框（placeholder=「请输入手机号」）
    "phone_input": os.getenv("XHS_SMS_PHONE_INPUT")
    or "input[placeholder*='手机号'], input[placeholder*='phone' i], input[type='tel'], input[name='phone']",
    # 「获取验证码 / 发送验证码」按钮
    "send_code_button": os.getenv("XHS_SMS_SEND_BUTTON")
    or (
        ":text-is(\"获取验证码\"), :text-is(\"发送验证码\"), button:has-text(\"获取验证码\"), "
        "button:has-text(\"Get code\"), button:has-text(\"Send code\"), button:has-text(\"Verification code\")"
    ),
    # 「重新获取 / 重新发送 / 再次获取」按钮：3 分钟倒计时归零后点击会再次发短信。
    # 大多数小红书 UI 倒计时结束会让按钮文本变回「获取验证码」，所以 fallback 用
    # send_code_button 再点一次也能起到同样效果（service 层会做 fallback）。
    "resend_code_button": os.getenv("XHS_SMS_RESEND_BUTTON")
    or (
        ":text-is(\"重新获取\"), :text-is(\"重新发送\"), :text-is(\"再次获取\"), "
        "button:has-text(\"重新获取\")"
    ),
    # 验证码输入框（placeholder=「输入验证码」）
    "sms_input": os.getenv("XHS_SMS_CODE_INPUT")
    or "input[placeholder*='验证码'], input[placeholder*='code' i], input[name='code'], input[name='verifyCode']",
    # 登录提交大红按钮；显式排除「手机号登录」tab 文案，避免误匹配
    "submit_button": os.getenv("XHS_SMS_SUBMIT_BUTTON")
    or (
        "button:has-text(\"登录\"):not(:has-text(\"手机号\"))"
        ":not(:has-text(\"协议\")):not(:has-text(\"政策\")), "
        "button:has-text(\"Log in\"), button:has-text(\"Sign in\"), button[type='submit']"
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

# 登录成功判据：等到 cookies 里出现 ``web_session`` 且其值非空。
# 注意：**不能用 URL 判 "/explore"**，因为 _LOGIN_URL 自身就是
# https://www.xiaohongshu.com/explore，从打开页面那一刻起 page.url 就一直
# 包含 /explore，会立刻误判为已登录、收下游客 cookie，导致后续 selfinfo
# 拿到 ``payload_keys=['user_id', 'guest']`` → 'guest' 字段证明是游客身份。
_LOGIN_COOKIE_NAME = "web_session"
# **每次会话用全新 incognito context**：不再持久化 profile。
# 之前用 launch_persistent_context + 共享 user_data_dir 会让上次失败遗留的
# cookies / localStorage 污染下一次会话，导致小红书弹不一样的 modal、
# selector 失效。XHS_SMS_PERSIST_PROFILE=true 可以打开旧行为（仅排障用）。
_PERSIST_PROFILE = (os.getenv("XHS_SMS_PERSIST_PROFILE") or "").strip().lower() in (
    "1",
    "true",
    "yes",
)
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


class PlaywrightSmsLoginDriver(SmsLoginDriver):
    """基于 Playwright 的 SMS 自动登录驱动。"""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
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

        self._playwright = await async_playwright().start()

        if _PERSIST_PROFILE:
            # 排障用：旧行为，复用 profile（不推荐生产）
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            logger.warning(
                f"[sms_login] XHS_SMS_PERSIST_PROFILE=true，复用 profile {_PROFILE_DIR}；"
                "可能因上次状态污染导致 selector 失效。仅排障用。"
            )
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(_PROFILE_DIR),
                headless=_HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
        else:
            # **默认新行为**：每次会话独立 incognito context，避免上次 cookies/
            # localStorage 让小红书弹不一样的 modal 导致 selector 失效。
            self._browser = await self._playwright.chromium.launch(
                headless=_HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            # 模拟桌面 UA，避免被识别为 headless / 老版本
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 800},
                locale="zh-CN",
            )
            self._page = await self._context.new_page()
            logger.info(
                "[sms_login] 已启动全新 incognito context（每次会话独立）"
            )

        await self._page.goto(
            _LOGIN_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
        )
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
        """切国家码下拉：

        实测下拉是带搜索框的长列表（截图：placeholder="搜索国家/地区"），
        且很可能是虚拟滚动 → 不在视口的项目根本不在 DOM 里。
        所以**核心策略是先用搜索框过滤，再点唯一项**。

        步骤（一轮）：
        1. **幂等检查**：触发器当前已显示目标 cc_label → 直接 return True
        2. 点国家码触发器，下拉打开
        3. 等下拉渲染 + 搜索框出现
        4. 在搜索框输入 cc_label（如 "+852"）→ 列表过滤到 1-2 行
        5. 点 :text-is("+852") → 命中唯一可见行
        6. 失败兜底：直接点 option_selector（适用于无搜索框的 A/B 变体）

        返回 True 表示成功选中。
        """
        page = self._require_page()
        template = _SELECTORS["country_option_template"]
        option_selector = template.replace("+852", cc_label)
        search_selector = _SELECTORS["country_search_input"]

        # ---- 0. 幂等检查：触发器是否已经是目标国家码 ----
        # 节省一次"打开下拉 → 搜索 → 点击"的耗时；尤其重要：换号循环里
        # 第 2、3 个号沿用同一 page，无需重切。
        if await self._is_country_code_already_selected(cc_label):
            logger.info(
                f"[sms_login] 国家码触发器已显示 {cc_label}，跳过切换（幂等命中）"
            )
            return True

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
                if attempt == 1:
                    try:
                        await page.wait_for_timeout(1000)
                    except Exception:
                        pass
                continue

            logger.info(
                f"[sms_login] attempt={attempt} 国家码触发器已点击，等待下拉渲染"
            )
            try:
                await page.wait_for_timeout(400)
            except Exception:
                pass

            # ---- 优先路径：搜索框过滤 ----
            # 长列表 + 虚拟滚动场景下，:text-is("+852") 永远命不中（DOM 里
            # 没渲染该行）；必须先在搜索框输入区号让列表过滤到只剩目标行。
            search_used = False
            try:
                search_locator = await self._first_visible_locator(search_selector)
                if search_locator is not None:
                    await search_locator.fill(cc_label, timeout=2000)
                    logger.info(
                        f"[sms_login] 已在国家码搜索框输入 {cc_label}，等待列表过滤"
                    )
                    search_used = True
                    try:
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug(
                    f"[sms_login] 国家码搜索框不可用 ({exc})，回退直接点选项"
                )

            picked = await self._safe_click(option_selector, optional=True)
            if picked:
                logger.info(
                    f"[sms_login] ✓ 已选 {cc_label} "
                    f"(via {'search+click' if search_used else 'click'})"
                )
                return True
            logger.info(
                f"[sms_login] attempt={attempt} 选项 {cc_label} 未命中 "
                f"(selector={option_selector!r}, search_used={search_used})"
            )

            # 第一轮失败 → 等 modal 重新渲染再试
            if attempt == 1:
                try:
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass
        return False

    async def _is_country_code_already_selected(self, cc_label: str) -> bool:
        """触发器是否已经显示目标国家码（如 "+852"）。

        判定原理：
        - 下拉**关闭**时，页面上可见的 ``+852`` 文字节点只可能出现在触发器
          位置（下拉列表项要么 unmount 要么 ``display:none``）。
        - 因此 ``:text-is("+852"):visible`` 命中数 > 0 即说明触发器已选中。

        任何异常都返回 False，让上层走完整切换流程兜底。
        """
        page = self._require_page()
        try:
            locator = await self._first_visible_locator(_SELECTORS["country_selector"])
            if locator is not None:
                text = await locator.inner_text(timeout=500)
                return cc_label in text
            locator = page.locator(f':text-is("{cc_label}"):visible')
            return await locator.count() > 0
        except Exception as exc:
            logger.debug(
                f"[sms_login] 幂等检查异常 ({exc})，按需切换 {cc_label}"
            )
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

    async def click_resend_sms(self) -> None:
        """3 分钟倒计时结束后再次发送验证码。

        策略：
        1. 先尝试「重新获取」selector
        2. 找不到 → fallback 点「获取验证码」（小红书倒计时归零后按钮文本通常会变回）
        3. 都失败 → 抛 RuntimeError + 截图诊断
        """
        page = self._require_page()
        # 1) 优先「重新获取」
        try:
            clicked = await self._safe_click(
                _SELECTORS["resend_code_button"], optional=True
            )
        except Exception:
            clicked = False
        if clicked:
            logger.info("[sms_login] ✓ 已点击「重新获取」按钮")
            return
        # 2) fallback：倒计时归零后按钮通常变回「获取验证码」
        logger.info(
            "[sms_login] 未命中「重新获取」按钮，回退点「获取验证码」"
            "（倒计时归零后按钮文本通常变回）"
        )
        try:
            await page.click(_SELECTORS["send_code_button"], timeout=_NAV_TIMEOUT_MS)
            logger.info("[sms_login] ✓ 已点击「获取验证码」按钮（重发回退）")
        except Exception as exc:
            await self._diagnostic_dump("resend_sms_failed", reason=str(exc))
            raise RuntimeError(f"点击「重新获取/获取验证码」失败: {exc}") from exc

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
        """轮询等待真正登录成功，返回完整 cookies 串。

        判据（按优先级）：

        1. **必要条件**：context.cookies() 里出现 ``web_session`` 且其值长度
           >= 16 个字符。游客态 / 登录前的 explore 页只有 ``a1`` / ``webId`` /
           ``acw_tc``，**没有 web_session**；后台 selfinfo 也据此区分访客。
        2. **辅助条件**（命中 #1 后立刻确认，避免 racing）：登录 modal 已经
           关闭 —— 通过原来的"手机号输入框消失"判断。

        ``timeout_seconds`` 内不满足判据 → ``TimeoutError``。
        """
        if self._context is None:
            raise RuntimeError("Playwright context 尚未初始化")

        end = asyncio.get_event_loop().time() + max(5, timeout_seconds)
        last_diag = ""
        while asyncio.get_event_loop().time() < end:
            snapshot = await self._collect_cookies(log_diag=False, allow_fallback=False)
            if snapshot is None:
                last_diag = "collect_cookies_failed"
                await asyncio.sleep(1)
                continue

            web_session_value = self._cookie_value(
                snapshot.cookies_str, _LOGIN_COOKIE_NAME
            )

            if web_session_value and len(web_session_value) >= 16:
                if await self._login_form_visible():
                    last_diag = (
                        f"web_session_present_but_login_form_visible"
                        f"(len={len(web_session_value)})"
                    )
                else:
                    logger.info(
                        f"[sms_login] ✓ 登录成功（web_session len={len(web_session_value)}, "
                        f"cookies_count={snapshot.raw_count}）"
                    )
                    return snapshot
            else:
                last_diag = (
                    f"web_session_missing_or_short(len={len(web_session_value)})"
                )

            await asyncio.sleep(2)

        # 超时前 dump 一份截图便于排查
        await self._diagnostic_dump(
            "wait_login_success_timeout", reason=last_diag
        )
        raise TimeoutError(
            f"等待登录成功超时（{timeout_seconds}s）；最后状态：{last_diag}"
        )

    async def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 关闭顺序：context → browser → playwright
        try:
            if self._context:
                await self._context.close()
        except Exception as exc:
            logger.debug(f"PlaywrightSmsLoginDriver close context 异常: {exc}")
        try:
            if self._browser:
                await self._browser.close()
        except Exception as exc:
            logger.debug(f"PlaywrightSmsLoginDriver close browser 异常: {exc}")
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.debug(f"PlaywrightSmsLoginDriver stop pw 异常: {exc}")
        self._context = None
        self._browser = None
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

    @staticmethod
    def _selector_candidates(selector: str) -> list[str]:
        return [part.strip() for part in (selector or "").split(",") if part.strip()]

    async def _first_visible_locator(self, selector: str) -> Any:
        page = self._require_page()
        for candidate in self._selector_candidates(selector):
            try:
                locator = page.locator(candidate)
                count = await locator.count()
            except Exception:
                continue
            for idx in range(min(count, 8)):
                item = locator.nth(idx)
                try:
                    if await item.is_visible(timeout=500):
                        return item
                except Exception:
                    continue
        return None

    async def _safe_click(self, selector: str, *, optional: bool) -> bool:
        page = self._require_page()
        last_exc: Exception | None = None
        for candidate in self._selector_candidates(selector):
            try:
                locator = page.locator(candidate)
                count = await locator.count()
                for idx in range(min(count, 8)):
                    item = locator.nth(idx)
                    try:
                        if await item.is_visible(timeout=500):
                            await item.click(timeout=3000)
                            return True
                    except Exception as exc:
                        last_exc = exc
                if count > 0:
                    await locator.first.click(timeout=3000)
                    return True
            except Exception as exc:
                last_exc = exc
                continue
        try:
            await page.click(selector, timeout=3000)
            return True
        except Exception as exc:
            last_exc = exc
            if optional:
                logger.debug(f"selector 可选点击失败 selector={selector} err={last_exc}")
                return False
            raise

    async def _collect_cookies(
        self, *, log_diag: bool = True, allow_fallback: bool = True
    ) -> Optional[CookieSnapshot]:
        """收集 cookies 拼成 ``cookies_str``。

        关键：用 ``context.cookies(urls=[...])`` 而**不是**裸 ``cookies()``。
        裸调用会返回 context storage 里所有域的 cookies（包括 explore /
        登录 modal / xhr 调用累积的、不同子域的、已过期未清理的），
        拼出的 cookies_str 是个"垃圾袋"，可能出现：

        - 同名 cookie 跨子域共存（``.xiaohongshu.com`` vs
          ``.www.xiaohongshu.com``），拼接顺序让 selfinfo 接口拿到访客那份
        - 过期 cookie 仍出现在串里
        - 无关域的 cookie 混入（hero-sms / cdn 等）

        ``urls=[https://www.xiaohongshu.com/]`` 让 Playwright 按
        domain+path+expiry 过滤，等价于"浏览器真实发送给该 URL 的
        Cookie header"，跟手动 DevTools 复制出来的内容对齐。
        """
        if self._context is None:
            return None
        target_url = self._cookie_target_url()
        try:
            cookies = await self._context.cookies(urls=[target_url])
        except Exception as exc:
            logger.warning(f"读取 cookies 失败 url={target_url}: {exc}")
            return None
        if not cookies:
            if not allow_fallback:
                return None
            # 兜底：极少数情况下 url 过滤拿不到 cookie（比如 page 跳到了别的
            # host），降级到全量；带 warning 让排查时能看到。
            logger.warning(
                f"[sms_login] context.cookies(urls=[{target_url}]) 返回空，"
                "降级到全量 cookies()"
            )
            try:
                cookies = await self._context.cookies()
            except Exception as exc:
                logger.warning(f"全量 cookies 也读取失败: {exc}")
                return None
            if not cookies:
                return None
        # 诊断 log（不打 value，避免泄露 web_session）：按 name+domain
        # 列出每个 cookie 的元数据，帮助和手动登录的 DevTools cookies 对比。
        diag = [
            f"{(c.get('name') or '?')}@{(c.get('domain') or '-')}"
            f"(len={len(c.get('value') or '')})"
            for c in cookies
        ]
        if log_diag:
            logger.info(
                f"[sms_login] collected {len(cookies)} cookies for "
                f"{target_url}: {diag}"
            )
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
    def _cookie_value(cookies_str: str, name: str) -> str:
        for part in (cookies_str or "").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() == name:
                return value.strip()
        return ""

    @staticmethod
    def _cookie_target_url() -> str:
        try:
            from xhs_utils.xhs_util import xhs_api_base_url

            base = xhs_api_base_url().strip().rstrip("/")
            if base:
                return f"{base}/"
        except Exception:
            pass
        return _LOGIN_URL

    async def _login_form_visible(self) -> bool:
        if self._page is None:
            return False
        for selector in (_SELECTORS["sms_input"], _SELECTORS["phone_input"]):
            try:
                locator = self._page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _country_iso_to_label(iso: str) -> str:
        mapping = {
            "HK": "+852",
            "CN": "+86",
            "US": "+1",
            "GB": "+44",
            "TW": "+886",
            "MO": "+853",
            "SG": "+65",
            "MY": "+60",
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
