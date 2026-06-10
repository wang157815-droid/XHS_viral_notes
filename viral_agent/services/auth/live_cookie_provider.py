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
    # 防止 Chrome 在后台节流 / 冻结标签页（避免长时间运行后浏览器进程被杀）
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-networking",
    "--disable-hang-monitor",
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
        """浏览器进程崩溃或外部关闭时自动触发，只清 context 引用。

        不停止 playwright server：playwright 进程仍然存活，
        _ensure_context 直接用它启动新 context，避免 stop→start 导致文件锁冲突。
        """
        logger.warning(
            "[LiveCookie] {} context 意外关闭（浏览器崩溃？），将在下次使用时自动重建",
            self._username,
        )
        self._context = None

    async def _ensure_context(self) -> None:
        """确保持久化 context 已启动且存活。"""
        if self._context is not None:
            # 1. 检查 Playwright 内部 _closed 标志（同步，无 CDP 开销）
            is_closed = False
            try:
                impl = self._context._impl_obj  # type: ignore[attr-defined]
                is_closed = bool(getattr(impl, "_closed", False))
            except Exception:
                pass

            if not is_closed:
                # 2. 真实 CDP 探活：context.cookies() 走 CDP round-trip
                try:
                    await asyncio.wait_for(self._context.cookies(), timeout=3.0)
                    return  # context 存活，直接返回
                except Exception as _probe_err:
                    logger.warning(
                        "[LiveCookie] {} context CDP 探活失败，重建: {}", self._username, _probe_err
                    )
            else:
                logger.warning("[LiveCookie] {} context 内部已关闭，重建", self._username)

            # context 死掉：只关闭 context，保留 playwright server（不要 stop）
            # stop playwright 会杀掉 Chrome 管理进程，再 launch 时 user-data-dir 可能还有锁
            dead_ctx = self._context
            self._context = None
            try:
                await dead_ctx.close()
            except Exception:
                pass
            # 等待 Chrome 进程完全退出并释放 user-data-dir 文件锁
            logger.info("[LiveCookie] {} 等待旧 Chrome 进程释放文件锁...", self._username)
            await asyncio.sleep(4.0)

        # ── context 为 None，需要重建 ───────────────────────────────────────────

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

        # 启动 playwright server（如果之前被停止过）
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        # launch_persistent_context 带重试（profile lock 偶发，等待后重试可恢复）
        is_headless = _is_server_mode()
        last_exc: Exception = RuntimeError("launch_persistent_context 未尝试")
        for _attempt in range(3):
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._user_data_dir),
                    headless=is_headless,
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_BROWSER_UA,
                    locale="zh-CN",
                    args=_BROWSER_ARGS + ([] if not is_headless else []),
                )
                break  # 成功
            except Exception as _launch_err:
                last_exc = _launch_err
                if _attempt < 2:
                    logger.warning(
                        "[LiveCookie] {} launch_persistent_context 第{}次失败（等待 4s 后重试）: {}",
                        self._username, _attempt + 1, _launch_err,
                    )
                    await asyncio.sleep(4.0)
                else:
                    logger.error(
                        "[LiveCookie] {} launch_persistent_context 3次均失败，放弃: {}",
                        self._username, _launch_err,
                    )
                    raise last_exc
        else:
            raise last_exc  # for 循环正常结束但没有 break（不应发生）

        if _STEALTH_JS_PATH.exists():
            await context.add_init_script(path=str(_STEALTH_JS_PATH))
            logger.debug("[LiveCookie] {} stealth.min.js 已注入", self._username)
        else:
            logger.warning("[LiveCookie] stealth.min.js 不存在: {}", _STEALTH_JS_PATH)

        # 注册崩溃自愈监听器：浏览器一挂就把 _context 置 None，下次调用自动重建
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
        """关闭 context + playwright，释放全部浏览器资源（供公开 close() 调用）。"""
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
        if ctx or pw:
            await asyncio.sleep(2.0)


def _is_server_mode() -> bool:
    import sys
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
