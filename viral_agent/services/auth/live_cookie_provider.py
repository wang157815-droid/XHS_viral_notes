"""LiveCookieProvider — 活浏览器 Cookie 提供器。

复刻 MediaCrawler 核心优势：cookie 从**活浏览器上下文实时取**，而非用静态 cookies.json 死值。

原理：
- 复用 browser_data/<username> 的已登录持久化 Profile（无需重扫码）
- 注入 stealth.min.js 降低检测风险
- 每次取 cookie 时 goto 小红书主页 → XHS 服务端下发最新 Set-Cookie → context.cookies()
- 结果写回 cookies.json，后续静态回退路径也保持最新

启用条件（环境变量）：
    LIVE_COOKIE_ENABLED=true   — 总开关（默认 false，Playwright 不可用时自动关闭）
    XHS_WEB_ORIGIN             — goto 的目标域（默认 https://www.xiaohongshu.com）

使用方式：
    provider = await LiveCookieProvider.get_for_identity(username, cookies_path)
    fresh = await provider.get_fresh_cookies()
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

# ── 常量（复用 qrcode_login_service 的设置）─────────────────────────────────
# 文件位于 viral_agent/services/auth/，parents[3] 才是项目根 XHS_viral_notes
# （与 qrcode_login_service.py 保持一致；parents[4] 会指向上一级目录导致路径错误）
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STEALTH_JS_PATH = _REPO_ROOT / "libs" / "stealth.min.js"
_USER_DATA_BASE_DIR = _REPO_ROOT / "browser_data"

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
_GOTO_URL = os.environ.get("XHS_WEB_ORIGIN", "https://www.xiaohongshu.com")
_REFRESH_TIMEOUT_MS = 30_000


def _live_cookie_enabled() -> bool:
    return os.environ.get("LIVE_COOKIE_ENABLED", "false").lower() in ("1", "true", "yes")


class LiveCookieProvider:
    """活浏览器 Cookie 提供器（per-identity 单例，崩溃自重启）。"""

    # 全局单例注册表：username → instance
    _registry: Dict[str, "LiveCookieProvider"] = {}
    _registry_lock = asyncio.Lock()

    def __init__(self, username: str, cookies_path: Path) -> None:
        self._username = username
        self._cookies_path = cookies_path
        self._user_data_dir = _USER_DATA_BASE_DIR / username
        self._playwright = None
        self._context = None
        self._lock = asyncio.Lock()

    # ── 工厂方法（per-identity 单例）─────────────────────────────────────────

    @classmethod
    async def get_for_identity(
        cls,
        username: str,
        cookies_path: Path,
    ) -> "LiveCookieProvider":
        async with cls._registry_lock:
            if username not in cls._registry:
                cls._registry[username] = cls(username, cookies_path)
            return cls._registry[username]

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def get_fresh_cookies(self) -> Optional[str]:
        """返回最新 cookie 字符串，失败时返回 None（调用方回退静态文件）。"""
        async with self._lock:
            try:
                await self._ensure_context()
                return await self._fetch_cookies_from_browser()
            except Exception as exc:
                logger.warning("[LiveCookie] {} 取 cookie 失败，尝试重启: {}", self._username, exc)
                await self._close_context()
                try:
                    await self._ensure_context()
                    return await self._fetch_cookies_from_browser()
                except Exception as exc2:
                    logger.error("[LiveCookie] {} 重启后仍失败，回退静态 cookie: {}", self._username, exc2)
                    return None

    async def close(self) -> None:
        """释放浏览器资源。"""
        async with self._lock:
            await self._close_context()

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    def _on_context_close(self) -> None:
        """浏览器进程崩溃或外部关闭时自动触发，重置 context 引用。"""
        logger.warning(
            "[LiveCookie] {} context 意外关闭（浏览器崩溃？），将在下次使用时自动重建",
            self._username,
        )
        self._context = None
        # _playwright 也同步清理，避免复用已断开的 playwright 实例
        self._playwright = None

    async def _ensure_context(self) -> None:
        """确保持久化 context 已启动且存活。"""
        if self._context is not None:
            # 快速健康检查：若 Playwright 内部 transport 已断开则重建
            try:
                _ = self._context.pages  # 同步属性，不发 CDP 消息，断连时会抛异常
            except Exception as _probe_err:
                logger.warning(
                    "[LiveCookie] {} context 探活失败，重建: {}", self._username, _probe_err
                )
                await self._close_context()
            else:
                return  # context 健在，直接返回

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("Playwright 未安装，无法使用 LiveCookieProvider")

        if not _USER_DATA_BASE_DIR.exists():
            _USER_DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)

        if not self._user_data_dir.exists():
            raise RuntimeError(
                f"[LiveCookie] browser_data/{self._username} 不存在，"
                "请先通过扫码登录建立持久化 Profile，再启用 LIVE_COOKIE_ENABLED"
            )

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        is_headless = _is_server_mode()
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._user_data_dir),
            headless=is_headless,
            viewport={"width": 1920, "height": 1080},
            user_agent=_BROWSER_UA,
            locale="zh-CN",
            args=_BROWSER_ARGS + ([] if not is_headless else []),
        )

        if _STEALTH_JS_PATH.exists():
            await context.add_init_script(path=str(_STEALTH_JS_PATH))
            logger.debug("[LiveCookie] {} stealth.min.js 已注入", self._username)
        else:
            logger.warning("[LiveCookie] stealth.min.js 不存在: {}", _STEALTH_JS_PATH)

        # 注册崩溃自愈监听器：浏览器一挂就把 _context 置 None，下次自动重建
        context.on("close", lambda: self._on_context_close())

        self._context = context
        logger.info("[LiveCookie] {} 持久化 context 已启动 headless={}", self._username, is_headless)

    async def _fetch_cookies_from_browser(self) -> str:
        """打开主页触发 Set-Cookie，返回 cookie 字符串并写回 cookies.json。"""
        page = None
        try:
            page = await self._context.new_page()
            await page.goto(
                _GOTO_URL,
                wait_until="domcontentloaded",
                timeout=_REFRESH_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.debug("[LiveCookie] {} goto 异常（非致命，继续取 cookie）: {}", self._username, exc)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

        raw_cookies = await self._context.cookies()
        if not raw_cookies:
            raise RuntimeError("context.cookies() 返回空列表，登录态可能已失效")

        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in raw_cookies)
        logger.debug(
            "[LiveCookie] {} 取到 {} 条 cookie，cookie_len={}",
            self._username, len(raw_cookies), len(cookie_str),
        )

        # 写回 cookies.json，保持静态回退路径也是最新值
        await asyncio.to_thread(self._write_back_cookie, cookie_str)

        return cookie_str

    def _write_back_cookie(self, cookie_str: str) -> None:
        try:
            import json
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"cookie": cookie_str}
            self._cookies_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("[LiveCookie] {} cookie 已写回 {}", self._username, self._cookies_path)
        except Exception as exc:
            logger.warning("[LiveCookie] {} 写回 cookies.json 失败: {}", self._username, exc)

    async def _close_context(self) -> None:
        ctx = self._context
        self._context = None
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        pw = self._playwright
        self._playwright = None
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


def _is_server_mode() -> bool:
    import sys
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
