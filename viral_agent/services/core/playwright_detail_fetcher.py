"""PlaywrightDetailFetcher — 使用真实 Chrome 获取笔记详情。

解决核心问题：
  bare HTTP 请求缺乏浏览器指纹（Canvas/WebGL/TLS fingerprint），
  XHS WAF 即使在住宅 IP 下也会对非浏览器请求返回 461/471。
  本模块使用 Playwright 控制真实 Chrome 导航到笔记页面，
  通过 page.evaluate() 提取 window.__INITIAL_STATE__ 中的笔记数据。

设计要点：
  - 复用 LiveCookieProvider 的持久化 browser context（stealth 已注入，cookies 共享）
  - 若 LiveCookieProvider 不可用则自建临时 context
  - 每次 fetch 开一个新 Page，完成后立即关闭（避免内存累积）
  - 并发控制：Semaphore 限制同时打开的页面数（默认 2）
  - 由 CDP_DETAIL_ENABLED=true 开关控制，默认关闭

启用条件：
  CDP_DETAIL_ENABLED=true    — 总开关（默认 false）
  LIVE_COOKIE_ENABLED=true   — 推荐同时开启，确保 cookies 来自真实浏览器
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

# ── 常量 ─────────────────────────────────────────────────────────────────────
# 文件位于 viral_agent/services/core/，parents[3] 才是项目根 XHS_viral_notes
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STEALTH_JS_PATH = _REPO_ROOT / "libs" / "stealth.min.js"
_USER_DATA_BASE_DIR = _REPO_ROOT / "browser_data"

_CDP_ENABLED = os.environ.get("CDP_DETAIL_ENABLED", "false").lower() in ("1", "true", "yes")
_PAGE_TIMEOUT_MS = int(os.environ.get("CDP_PAGE_TIMEOUT_MS", "20000"))   # 页面加载超时
_MAX_CONCURRENT = int(os.environ.get("CDP_DETAIL_CONCURRENCY", "2"))     # 同时打开的页面数

_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--lang=zh-CN",
]
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)


def cdp_detail_enabled() -> bool:
    """是否启用 CDP 详情获取（运行时读取，支持热切换）。"""
    return os.environ.get("CDP_DETAIL_ENABLED", "false").lower() in ("1", "true", "yes")


class CDPContextUnavailableError(Exception):
    """browser_data 目录不存在或 LiveCookieProvider 未就绪，CDP context 无法获取。

    常见原因：
    - 用户通过虚拟号（SMS）登录，没有 browser_data/<username> 持久化 Profile
    - QR 扫码还未完成，browser_data 目录尚未创建
    调用方应捕获此异常并降级为 HTTP 模式。
    """
    pass


class PlaywrightDetailFetcher:
    """用真实 Chrome 获取笔记详情的单例 fetcher（per-identity）。"""

    _registry: Dict[str, "PlaywrightDetailFetcher"] = {}
    _registry_lock = asyncio.Lock()
    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self, username: str) -> None:
        self._username = username
        self._own_context = None          # 仅在无法复用 LiveCookieProvider 时使用
        self._own_playwright = None
        self._lock = asyncio.Lock()

    # ── 工厂方法 ─────────────────────────────────────────────────────────────

    @classmethod
    async def get_for_identity(cls, username: str) -> "PlaywrightDetailFetcher":
        async with cls._registry_lock:
            if username not in cls._registry:
                cls._registry[username] = cls(username)
            if cls._semaphore is None:
                cls._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
            return cls._registry[username]

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def fetch(self, note_url: str) -> Optional[Dict]:
        """打开笔记页面，提取 window.__INITIAL_STATE__ 中的笔记数据。

        成功返回笔记 dict；失败（CAPTCHA / JS未渲染 / 解析失败）返回 None。
        """
        semaphore = PlaywrightDetailFetcher._semaphore or asyncio.Semaphore(_MAX_CONCURRENT)
        async with semaphore:
            context = await self._get_context()
            if context is None:
                raise CDPContextUnavailableError(
                    f"无法获取 browser context（browser_data/{self._username} 不存在或 "
                    "LiveCookieProvider 未就绪）。虚拟号/SMS 登录用户请使用 HTTP 模式。"
                )

            page = None
            try:
                page = await context.new_page()
                result = await self._navigate_and_extract(page, note_url)
                return result
            except Exception as exc:
                logger.warning(f"[CDP] 导航异常: {exc}, url={note_url}")
                return None
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def close(self) -> None:
        """释放自建的 browser context（复用 LiveCookieProvider 的不释放）。"""
        async with self._lock:
            ctx = self._own_context
            self._own_context = None
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass
            pw = self._own_playwright
            self._own_playwright = None
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    async def _get_context(self):
        """优先复用 LiveCookieProvider 的 context；否则自建持久化 context。

        LiveCookieProvider._registry 键是 username，而 self._username 传入的可能是
        owner_user_id（u_xxx）。优先尝试直接匹配，失败则通过 credential_resolver 映射。

        LIVE_COOKIE_ENABLED=true 时，LiveCookieProvider 可能还在初始化中（竞争启动），
        此时若立即自建 context 会与 LiveCookieProvider 争抢同一个 profile 目录，
        导致 Chromium "browser has been closed" 报错。
        因此 LIVE_COOKIE_ENABLED=true 时最多等待 10s 再决定是否自建。
        """
        import os as _os
        live_cookie_enabled = _os.environ.get("LIVE_COOKIE_ENABLED", "false").lower() in ("1", "true", "yes")

        # 方案 A：复用 LiveCookieProvider 的 context（stealth 已注入，cookies 共享）
        # 若 LIVE_COOKIE_ENABLED=true 但 context 尚未就绪，则轮询等待，避免竞争 profile 目录
        max_wait = 10.0 if live_cookie_enabled else 0.0
        waited = 0.0
        while True:
            try:
                from viral_agent.services.auth.live_cookie_provider import LiveCookieProvider
                provider = LiveCookieProvider._registry.get(self._username)
                if provider is None:
                    resolved_username = _resolve_username_from_owner(self._username)
                    if resolved_username and resolved_username != self._username:
                        provider = LiveCookieProvider._registry.get(resolved_username)
                if provider and provider._context is not None:
                    return provider._context
            except Exception:
                pass

            if waited >= max_wait:
                break
            await asyncio.sleep(0.5)
            waited += 0.5

        # 方案 B：自建 context（LiveCookieProvider 未启动/未启用时的回退）
        # LIVE_COOKIE_ENABLED=true 且 profile 目录存在时跳过，避免与 LiveCookieProvider 抢目录
        if live_cookie_enabled:
            resolved_username = _resolve_username_from_owner(self._username)
            profile_dir = _USER_DATA_BASE_DIR / (resolved_username or self._username)
            if profile_dir.exists():
                logger.warning(
                    f"[CDP] LIVE_COOKIE_ENABLED=true 但等待 {max_wait}s 后 LiveCookieProvider 仍未就绪，"
                    "跳过自建 context 避免抢占 profile 目录。CDP 本次不可用。"
                )
                return None

        async with self._lock:
            if self._own_context is not None:
                return self._own_context

            user_data_dir = _USER_DATA_BASE_DIR / self._username
            if not user_data_dir.exists():
                logger.warning(
                    f"[CDP] browser_data/{self._username} 不存在，"
                    "无法启动 CDP 模式。请先扫码登录建立持久化 Profile。"
                )
                return None

            try:
                from playwright.async_api import async_playwright
            except ImportError:
                logger.error("[CDP] Playwright 未安装，无法使用 CDP 详情获取")
                return None

            try:
                self._own_playwright = await async_playwright().start()
                is_headless = _is_server_mode()
                context = await self._own_playwright.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=is_headless,
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_BROWSER_UA,
                    locale="zh-CN",
                    args=_BROWSER_ARGS,
                )
                if _STEALTH_JS_PATH.exists():
                    await context.add_init_script(path=str(_STEALTH_JS_PATH))
                    logger.debug(f"[CDP] stealth.min.js 已注入 ({self._username})")
                self._own_context = context
                logger.info(f"[CDP] 自建持久化 context 已启动 headless={is_headless} ({self._username})")
                return self._own_context
            except Exception as exc:
                logger.error(f"[CDP] 启动 browser context 失败: {exc}")
                if self._own_playwright:
                    try:
                        await self._own_playwright.stop()
                    except Exception:
                        pass
                    self._own_playwright = None
                return None

    async def _navigate_and_extract(self, page, note_url: str) -> Optional[Dict]:
        """导航到笔记页面并提取数据。"""
        from apis.xhs_pc_apis import CaptchaError
        try:
            await page.goto(
                note_url,
                wait_until="domcontentloaded",
                timeout=_PAGE_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.debug(f"[CDP] page.goto 超时/异常（继续尝试提取）: {exc}")

        # 检查是否触发了 CAPTCHA 页面 → 抛 CaptchaError，触发上层退避重试
        current_url = page.url
        if "verify" in current_url or "captcha" in current_url.lower():
            logger.warning(f"[CDP] 页面重定向到验证页: {current_url}")
            raise CaptchaError(f"CDP detail: 页面跳转到验证页 {current_url}")

        # 方法 1：通过 JS 直接读取 window.__INITIAL_STATE__（最可靠）
        try:
            state = await page.evaluate("""() => {
                const raw = window.__INITIAL_STATE__;
                if (!raw) return null;
                return JSON.parse(JSON.stringify(raw,
                    (k, v) => v === undefined ? null : v));
            }""")
            if state:
                note_id = note_url.split("?")[0].rstrip("/").split("/")[-1]
                note = (
                    state.get("note", {})
                    .get("noteDetailMap", {})
                    .get(note_id, {})
                    .get("note")
                )
                if note:
                    logger.info(f"[CDP] JS evaluate 成功: note_id={note_id}")
                    return note
        except Exception as exc:
            logger.debug(f"[CDP] JS evaluate 失败，回退 HTML 解析: {exc}")

        # 方法 2：从 HTML 内容提取（SSR 场景兜底）
        try:
            html = await page.content()
            return _parse_initial_state_from_html(html, note_url)
        except Exception as exc:
            logger.debug(f"[CDP] HTML 内容提取失败: {exc}")
            return None


# ── 独立工具函数（供 _html_fallback 和本模块共用）────────────────────────────

def _parse_initial_state_from_html(html: str, note_url: str) -> Optional[Dict]:
    """从 HTML 中提取 window.__INITIAL_STATE__ 并解析笔记数据。"""
    if "window.__INITIAL_STATE__" not in html or "noteDetailMap" not in html:
        return None

    js_str = _extract_initial_state_json(html)
    if not js_str:
        return None

    try:
        state = json.loads(
            js_str
            .replace(":undefined", ":null")
            .replace(":Undefined", ":null")
        )
    except json.JSONDecodeError:
        return None

    note_id = note_url.split("?")[0].rstrip("/").split("/")[-1]
    return (
        state.get("note", {})
        .get("noteDetailMap", {})
        .get(note_id, {})
        .get("note")
    )


def _extract_initial_state_json(html: str) -> Optional[str]:
    """用括号栈计数器从 HTML 中安全提取 __INITIAL_STATE__ 的完整 JSON。"""
    marker = "window.__INITIAL_STATE__="
    idx = html.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    depth = 0
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    return None


def _is_server_mode() -> bool:
    import sys
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _resolve_username_from_owner(owner_user_id: str) -> str:
    """将 RedMuse owner_user_id 映射到 browser_data 使用的 username。

    LiveCookieProvider._registry 键是 username，而调用方传入的可能是 u_xxx 格式的
    RedMuse user_id。通过 credential_resolver 做一次映射；失败时返回原始值。
    """
    try:
        from backend.app.services.xhs_auth.credential_resolver import get_credential_resolver
        resolver = get_credential_resolver()
        username, _ = resolver._resolve_username_and_cookies_path(owner_user_id)
        return username or owner_user_id
    except Exception:
        return owner_user_id
